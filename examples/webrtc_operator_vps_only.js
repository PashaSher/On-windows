/**
 * ICE + RTCPeerConnection через Hetzner VPS (coturn + /api/ice).
 * @see webrtc_ice_operator_fetch.js
 */

import { fetchOperatorIceServers } from "../webrtc_ice_operator_fetch.js";

export function filterTurnIceServers(iceServers) {
    return iceServers.filter((s) => {
        const urls = Array.isArray(s.urls) ? s.urls : [s.urls];
        return urls.some((u) => /turns?:/i.test(String(u || "")));
    });
}

/**
 * @param {string} iceConfigUrl
 * @param {string} [iceConfigToken]
 * @param {{ relayOnly?: boolean }} [opts]
 */
export async function loadVpsIceServers(iceConfigUrl, iceConfigToken = "", opts = {}) {
    const relayOnly = opts.relayOnly !== false;
    let result = await fetchOperatorIceServers({
        iceConfigUrl,
        iceConfigToken,
        hetznerRelayOnly: relayOnly,
    });
    if (relayOnly && result.source === "api") {
        const turnOnly = filterTurnIceServers(result.iceServers);
        if (turnOnly.length === 0) {
            result = { iceServers: [], source: "stun-only", error: "no TURN servers in API response" };
        } else {
            result = { ...result, iceServers: turnOnly };
        }
    }
    return result;
}

/**
 * Hetzner-only: TURN + iceTransportPolicy relay (как на Pi в VPS-only режиме).
 */
export async function createVpsOnlyPeerConnection(iceConfigUrl, iceConfigToken, options = {}) {
    return createOperatorPeerConnection(iceConfigUrl, iceConfigToken, {
        relayOnly: true,
        ...options,
    });
}

/**
 * @param {string} iceConfigUrl
 * @param {string} [iceConfigToken]
 * @param {{ relayOnly?: boolean, iceCandidatePoolSize?: number }} [options]
 * @returns {Promise<{ pc: RTCPeerConnection, iceProbe: object }>}
 */
/** Убрать host/srflx из SDP answer (для браузера с iceTransportPolicy=relay). */
export function filterSdpIceRelayOnly(sdp) {
    if (!sdp) {
        return sdp;
    }
    const lines = sdp.split(/\r\n|\n/);
    const out = lines.filter((line) => {
        if (!line.startsWith("a=candidate:")) {
            return true;
        }
        return /\btyp relay\b/i.test(line);
    });
    return out.join("\r\n");
}

export async function createOperatorPeerConnection(iceConfigUrl, iceConfigToken, options = {}) {
    const relayOnly = !!options.relayOnly;
    const iceProbe = await loadVpsIceServers(iceConfigUrl, iceConfigToken, { relayOnly });
    if (relayOnly && (iceProbe.source !== "api" || iceProbe.iceServers.length === 0)) {
        const err = new Error(iceProbe.error || "VPS ICE / TURN unavailable");
        err.iceProbe = iceProbe;
        throw err;
    }
    const pcConfig = {
        iceServers: iceProbe.iceServers,
        iceCandidatePoolSize: options.iceCandidatePoolSize ?? 10,
    };
    if (relayOnly) {
        pcConfig.iceTransportPolicy = "relay";
    }
    return { pc: new RTCPeerConnection(pcConfig), iceProbe };
}
