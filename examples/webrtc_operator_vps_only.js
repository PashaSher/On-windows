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

/** Только turn:…?transport=tcp — браузер не использует UDP relay-порты 49160+. */
export function filterTurnTcpTransportOnly(iceServers) {
    return iceServers
        .map((s) => {
            const urls = Array.isArray(s.urls) ? s.urls : [s.urls];
            const tcp = urls.filter((u) => /transport=tcp/i.test(String(u)));
            if (tcp.length === 0) {
                return null;
            }
            return { ...s, urls: tcp.length === 1 ? tcp[0] : tcp };
        })
        .filter(Boolean);
}

/** TCP TURN первым — обходит блокировку UDP relay у части провайдеров. */
export function sortTurnTcpFirst(iceServers) {
    return [...iceServers].sort((a, b) => {
        const score = (s) => {
            const urls = Array.isArray(s.urls) ? s.urls : [s.urls];
            return urls.some((u) => /transport=tcp/i.test(String(u))) ? 0 : 1;
        };
        return score(a) - score(b);
    });
}

/**
 * @param {string} iceConfigUrl
 * @param {string} [iceConfigToken]
 * @param {{ relayOnly?: boolean, tcpOnly?: boolean }} [opts]
 */
export async function loadVpsIceServers(iceConfigUrl, iceConfigToken = "", opts = {}) {
    const relayOnly = opts.relayOnly !== false;
    const tcpOnly = !!opts.tcpOnly;
    let result = await fetchOperatorIceServers({
        iceConfigUrl,
        iceConfigToken,
        hetznerRelayOnly: relayOnly,
    });
    if (relayOnly && result.source === "api") {
        let turnOnly = filterTurnIceServers(result.iceServers);
        if (tcpOnly) {
            turnOnly = filterTurnTcpTransportOnly(turnOnly);
        }
        if (turnOnly.length === 0) {
            result = {
                iceServers: [],
                source: "stun-only",
                error: tcpOnly ? "no TCP TURN in API response" : "no TURN servers in API response",
            };
        } else {
            result = { ...result, iceServers: sortTurnTcpFirst(turnOnly) };
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
 * @param {{ relayOnly?: boolean, tcpOnly?: boolean, iceCandidatePoolSize?: number }} [options]
 * @returns {Promise<{ pc: RTCPeerConnection, iceProbe: object }>}
 */
/** Убрать host/srflx из SDP (для iceTransportPolicy=relay). */
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

/** Оставить только TCP relay (typ relay + « tcp »), убрать UDP relay 49160+. */
export function filterSdpIceTcpRelayOnly(sdp) {
    if (!sdp) {
        return sdp;
    }
    const lines = sdp.split(/\r\n|\n/);
    const out = lines.filter((line) => {
        if (!line.startsWith("a=candidate:")) {
            return true;
        }
        if (!/\btyp relay\b/i.test(line)) {
            return false;
        }
        return /\s1\s+tcp\s+/i.test(line);
    });
    return out.join("\r\n");
}

export function isTcpRelayCandidateLine(line) {
    const s = String(line || "");
    return /\btyp relay\b/i.test(s) && /\s1\s+tcp\s+/i.test(s);
}

export function isUdpRelayCandidateLine(line) {
    const s = String(line || "");
    return /\btyp relay\b/i.test(s) && /\s1\s+udp\s+/i.test(s);
}

export async function createOperatorPeerConnection(iceConfigUrl, iceConfigToken, options = {}) {
    const relayOnly = !!options.relayOnly;
    const iceProbe = await loadVpsIceServers(iceConfigUrl, iceConfigToken, {
        relayOnly,
        tcpOnly: !!options.tcpOnly,
    });
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
