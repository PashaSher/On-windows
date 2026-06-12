/**
 * Trickle ICE через VPS signaling API.
 */

import { signalPostCallerCandidate, signalPostCalleeCandidate } from "./webrtc_vps_signaling.js";

function parseCandidateTyp(line) {
    const m = String(line || "").match(/\btyp\s+(\w+)/i);
    return m ? m[1].toLowerCase() : "other";
}

/**
 * @param {string} apiBase
 * @param {string} roomIdOrPath
 * @param {RTCPeerConnection} pc
 * @param {'caller'|'callee'} role
 */
export function attachVpsIceTrickle(apiBase, roomIdOrPath, pc, role, hooks = {}) {
    const remoteRelayOnly = !!hooks.remoteRelayOnly;
    const localTcpRelayOnly = !!hooks.localTcpRelayOnly;
    const iceToken = hooks.iceToken || "";
    const isCaller = role === "caller";
    const postRemote = isCaller ? signalPostCallerCandidate : signalPostCalleeCandidate;
    const remoteKey = isCaller ? "calleeCandidates" : "callerCandidates";

    const pending = [];
    const pendingUdpRelay = [];
    const appliedKeys = new Set();
    let tcpRelayPosted = false;
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
        const line = event.candidate.candidate || "";
        const isUdpRelay =
            localTcpRelayOnly && /\btyp relay\b/i.test(line) && /\s1\s+udp\s+/i.test(line);
        const isTcpRelay =
            localTcpRelayOnly && /\btyp relay\b/i.test(line) && /\s1\s+tcp\s+/i.test(line);
        if (isUdpRelay && !tcpRelayPosted) {
            pendingUdpRelay.push(event.candidate);
            hooks.onLocalCandidate?.(event.candidate, null, { skipped: true, reason: "udp-relay-queued" });
            return;
        }
        if (isUdpRelay) {
            hooks.onLocalCandidate?.(event.candidate, null, { skipped: true, reason: "udp-relay" });
            return;
        }
        const raw = event.candidate.toJSON();
        const post = isCaller ? signalPostCallerCandidate : signalPostCalleeCandidate;
        void post(apiBase, roomIdOrPath, raw, iceToken).catch((e) => hooks.onError?.(e));
        if (isTcpRelay) {
            tcpRelayPosted = true;
        }
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

    function applySnapshotCandidates(snapshot) {
        const bag = snapshot?.[remoteKey];
        if (!bag || typeof bag !== "object") {
            return Promise.resolve(0);
        }
        let chain = Promise.resolve(0);
        for (const raw of Object.values(bag)) {
            chain = chain.then(async (n) => n + (await applyRemote(raw) ? 1 : 0));
        }
        return chain;
    }

    async function flushUdpRelayFallback() {
        if (!localTcpRelayOnly || tcpRelayPosted || pendingUdpRelay.length === 0) {
            return 0;
        }
        const post = isCaller ? signalPostCallerCandidate : signalPostCalleeCandidate;
        let n = 0;
        for (const cand of pendingUdpRelay) {
            const raw = cand.toJSON();
            await post(apiBase, roomIdOrPath, raw, iceToken).catch((e) => hooks.onError?.(e));
            hooks.onLocalCandidate?.(cand, raw, { fallback: true, reason: "udp-relay-fallback" });
            n += 1;
        }
        pendingUdpRelay.length = 0;
        return n;
    }

    return {
        detach() {
            pc.onicecandidate = prevOnIceCandidate;
        },
        flushPending,
        flushUdpRelayFallback,
        applySnapshotCandidates,
        notifyRemoteDescriptionSet: flushPending,
    };
}
