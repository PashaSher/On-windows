/**
 * Загрузка iceServers с VPS (GET /api/ice) для браузера-оператора.
 * Тот же контракт, что у Pi: Bearer и/или ?token= (ICE_CONFIG_TOKEN на сервере).
 */

export const DEFAULT_OPERATOR_STUN = [
    { urls: "stun:stun.l.google.com:19302" },
    { urls: "stun:stun1.l.google.com:19302" },
];

/**
 * @param {string} iceConfigUrl
 * @param {string} [iceConfigToken]
 * @returns {string}
 */
export function buildIceConfigRequestUrl(iceConfigUrl, iceConfigToken = "") {
    const raw = (iceConfigUrl || "").trim();
    if (!raw) {
        return "";
    }
    const tok = (iceConfigToken || "").trim();
    if (!tok) {
        return raw;
    }
    try {
        const u = new URL(raw, window.location.href);
        if (!u.searchParams.has("token")) {
            u.searchParams.set("token", tok);
        }
        return u.toString();
    } catch (_) {
        const sep = raw.includes("?") ? "&" : "?";
        return raw.includes("token=") ? raw : `${raw}${sep}token=${encodeURIComponent(tok)}`;
    }
}

/**
 * @param {{
 *   iceConfigUrl: string,
 *   iceConfigToken?: string,
 *   defaultStun?: Array<{urls: string}>,
 *   fetchImpl?: typeof fetch,
 *   hetznerRelayOnly?: boolean,
 * }} opts
 * @returns {Promise<{ iceServers: object[], source: "api"|"stun-only", error?: string }>}
 */
export async function fetchOperatorIceServers(opts) {
    const fetchFn = opts.fetchImpl || fetch;
    const baseStun = opts.defaultStun ?? DEFAULT_OPERATOR_STUN;
    const hetznerOnly = !!opts.hetznerRelayOnly;
    const iceUrl = buildIceConfigRequestUrl(opts.iceConfigUrl, opts.iceConfigToken);
    if (!iceUrl) {
        return { iceServers: hetznerOnly ? [] : baseStun.map((x) => ({ ...x })), source: "stun-only" };
    }

    const tok = (opts.iceConfigToken || "").trim();
    const headers = {};
    if (tok) {
        headers.Authorization = `Bearer ${tok}`;
    }

    try {
        const res = await fetchFn(iceUrl, { method: "GET", headers, credentials: "omit" });
        if (!res.ok) {
            return {
                iceServers: hetznerOnly ? [] : baseStun.map((x) => ({ ...x })),
                source: "stun-only",
                error: `HTTP ${res.status}`,
            };
        }
        const j = await res.json();
        if (j && Array.isArray(j.iceServers) && j.iceServers.length > 0) {
            const list = hetznerOnly ? j.iceServers : [...baseStun.map((x) => ({ ...x })), ...j.iceServers];
            list.sort((a, b) => {
                const au = String((Array.isArray(a.urls) ? a.urls[0] : a.urls) || "");
                const bu = String((Array.isArray(b.urls) ? b.urls[0] : b.urls) || "");
                const aTcp = /transport=tcp|turns:/i.test(au);
                const bTcp = /transport=tcp|turns:/i.test(bu);
                return (aTcp === bTcp ? 0 : aTcp ? -1 : 1);
            });
            return { iceServers: list, source: "api" };
        }
        return {
            iceServers: hetznerOnly ? [] : baseStun.map((x) => ({ ...x })),
            source: "stun-only",
            error: "empty iceServers",
        };
    } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        return {
            iceServers: hetznerOnly ? [] : baseStun.map((x) => ({ ...x })),
            source: "stun-only",
            error: msg,
        };
    }
}
