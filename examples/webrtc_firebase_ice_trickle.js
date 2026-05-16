/**
 * Trickle ICE через Firebase RTDB (caller ↔ callee).
 * Подключать ДО createOffer / setLocalDescription.
 */

import { get, push, onChildAdded, roomChildRef } from "./webrtc_firebase_rtdb.js";

function parseCandidateTyp(line) {
    const m = String(line || "").match(/\btyp\s+(\w+)/i);
    return m ? m[1].toLowerCase() : "other";
}

/**
 * @param {import('firebase/database').Database} db
 * @param {string} roomIdOrPath
 * @param {RTCPeerConnection} pc
 * @param {'caller'|'callee'} role
 */
export function attachFirebaseIceTrickle(db, roomIdOrPath, pc, role, hooks = {}) {
    const remoteRelayOnly = !!hooks.remoteRelayOnly;

    const isCaller = role === "caller";
    const localKey = isCaller ? "callerCandidates" : "calleeCandidates";
    const remoteKey = isCaller ? "calleeCandidates" : "callerCandidates";

    const pending = [];
    const appliedKeys = new Set();
    let prevOnIceCandidate = pc.onicecandidate;

    function rawKey(raw) {
        const c = raw?.candidate || "";
        return c.slice(0, 120) || JSON.stringify(raw);
    }

    pc.onicecandidate = (event) => {
        if (typeof prevOnIceCandidate === "function") {
            try {
                prevOnIceCandidate.call(pc, event);
            } catch (_) {}
        }
        if (!event.candidate) {
            return;
        }
        const raw = event.candidate.toJSON();
        push(roomChildRef(db, roomIdOrPath, localKey), raw);
        hooks.onLocalCandidate?.(event.candidate, raw);
    };

    async function applyRemote(raw) {
        if (!raw) {
            return false;
        }
        const key = rawKey(raw);
        if (appliedKeys.has(key)) {
            return false;
        }
        const typ = parseCandidateTyp(raw.candidate || "");
        if (remoteRelayOnly && typ !== "relay") {
            hooks.onRemoteCandidate?.(raw, { queued: false, typ, skipped: true });
            return false;
        }
        if (!pc.remoteDescription) {
            pending.push(raw);
            hooks.onRemoteCandidate?.(raw, { queued: true, typ });
            return false;
        }
        try {
            await pc.addIceCandidate(new RTCIceCandidate(raw));
            appliedKeys.add(key);
            hooks.onRemoteCandidate?.(raw, { queued: false, typ });
            return true;
        } catch (e) {
            hooks.onError?.(e instanceof Error ? e : new Error(String(e)));
            return false;
        }
    }

    async function flushPending() {
        let n = 0;
        while (pending.length > 0 && pc.remoteDescription) {
            const raw = pending.shift();
            if (await applyRemote(raw)) {
                n += 1;
            }
        }
        return n;
    }

    /** Все callee/caller ICE уже в Firebase (onChildAdded мог опоздать). */
    async function syncRemoteFromFirebase() {
        const snap = await get(roomChildRef(db, roomIdOrPath, remoteKey));
        const val = snap.val();
        if (!val || typeof val !== "object") {
            return 0;
        }
        let n = 0;
        for (const k of Object.keys(val)) {
            if (await applyRemote(val[k])) {
                n += 1;
            }
        }
        return n;
    }

    const unsubscribeRemote = onChildAdded(roomChildRef(db, roomIdOrPath, remoteKey), (snapshot) => {
        void applyRemote(snapshot.val());
    });

    return {
        detach() {
            unsubscribeRemote();
            pc.onicecandidate = prevOnIceCandidate;
        },
        flushPending,
        syncRemoteFromFirebase,
        notifyRemoteDescriptionSet: flushPending,
    };
}
