/**
 * WebRTC signaling через VPS (замена Firebase RTDB).
 */

export function normalizeRoomId(roomIdOrPath) {
    let r = String(roomIdOrPath || "").trim();
    if (!r) {
        r = "pi-camera";
    }
    if (r.startsWith("rooms/")) {
        r = r.slice(6);
    }
    return r;
}

export function defaultSignalApiBase() {
    const loc = window.location;
    if (loc.protocol.startsWith("http")) {
        return `${loc.origin.replace(/\/$/, "")}/api/signal`;
    }
    return "http://116.203.148.254/api/signal";
}

export function roomApiUrl(apiBase, roomIdOrPath, ...parts) {
    const room = encodeURIComponent(normalizeRoomId(roomIdOrPath));
    const tail = parts.length ? "/" + parts.map((p) => encodeURIComponent(p)).join("/") : "";
    return `${apiBase.replace(/\/$/, "")}/rooms/${room}${tail}`;
}

function authHeaders(iceToken = "") {
    const h = { "Content-Type": "application/json" };
    const tok = (iceToken || "").trim();
    if (tok) {
        h.Authorization = `Bearer ${tok}`;
    }
    return h;
}

export async function signalFetch(apiBase, roomId, pathParts, opts = {}) {
    const url = roomApiUrl(apiBase, roomId, ...pathParts);
    const res = await fetch(url, {
        cache: "no-store",
        ...opts,
        headers: { ...authHeaders(opts.iceToken), ...(opts.headers || {}) },
    });
    if (!res.ok) {
        const err = new Error(`signal ${res.status} ${url}`);
        err.status = res.status;
        throw err;
    }
    if (res.status === 204) {
        return null;
    }
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
        return res.json();
    }
    return null;
}

export async function signalWaitEvents(apiBase, roomId, since, timeoutSec = 25, iceToken = "") {
    const url =
        roomApiUrl(apiBase, roomId, "events") +
        `?since=${encodeURIComponent(String(since))}&timeout=${encodeURIComponent(String(timeoutSec))}`;
    const res = await fetch(url, { cache: "no-store", headers: authHeaders(iceToken) });
    if (!res.ok) {
        throw new Error(`signal events ${res.status}`);
    }
    return res.json();
}

export async function signalClearCallerSide(apiBase, roomId, iceToken = "") {
    const url = roomApiUrl(apiBase, roomId);
    const res = await fetch(url, {
        method: "DELETE",
        cache: "no-store",
        headers: { ...authHeaders(iceToken), "X-Clear": "caller" },
    });
    if (!res.ok) {
        throw new Error(`signal clear caller ${res.status}`);
    }
}

/** Сброс answer и ICE Pi (без удаления offer). Нужен перед новым Connect. */
export async function signalClearCalleeSide(apiBase, roomId, iceToken = "") {
    const url = roomApiUrl(apiBase, roomId);
    const res = await fetch(url, {
        method: "DELETE",
        cache: "no-store",
        headers: { ...authHeaders(iceToken), "X-Clear": "callee" },
    });
    if (!res.ok) {
        throw new Error(`signal clear callee ${res.status}`);
    }
}

/** Полный сброс offer/answer/ICE перед новым Connect. */
export async function signalClearSession(apiBase, roomId, iceToken = "") {
    await signalClearCallerSide(apiBase, roomId, iceToken);
    await signalClearCalleeSide(apiBase, roomId, iceToken);
}

/** Полный сброс комнаты (offer/answer/ICE) перед новым Connect. */
export async function signalClearRoom(apiBase, roomId, iceToken = "") {
    const url = roomApiUrl(apiBase, roomId);
    const res = await fetch(url, {
        method: "DELETE",
        cache: "no-store",
        headers: authHeaders(iceToken),
    });
    if (!res.ok) {
        throw new Error(`signal clear room ${res.status}`);
    }
}

export async function signalPutOffer(apiBase, roomId, offer, iceToken = "") {
    await signalFetch(apiBase, roomId, ["offer"], {
        method: "PUT",
        body: JSON.stringify(offer),
        iceToken,
    });
}

export async function signalPutAnswer(apiBase, roomId, answer, iceToken = "") {
    await signalFetch(apiBase, roomId, ["answer"], {
        method: "PUT",
        body: JSON.stringify(answer),
        iceToken,
    });
}

export async function signalPostCallerCandidate(apiBase, roomId, cand, iceToken = "") {
    await signalFetch(apiBase, roomId, ["caller-candidates"], {
        method: "POST",
        body: JSON.stringify(cand),
        iceToken,
    });
}

export async function signalPostCalleeCandidate(apiBase, roomId, cand, iceToken = "") {
    await signalFetch(apiBase, roomId, ["callee-candidates"], {
        method: "POST",
        body: JSON.stringify(cand),
        iceToken,
    });
}
