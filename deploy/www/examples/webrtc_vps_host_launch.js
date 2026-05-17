/**
 * Следит за перезапуском Pi через VPS signaling (host / needOffer).
 */

import { signalWaitEvents, normalizeRoomId } from "./webrtc_vps_signaling.js";

/**
 * @param {string} apiBase
 * @param {string} roomIdOrPath
 * @param {(info: object) => void} onRelaunch
 * @param {{ onPiWaiting?: () => void, iceToken?: string }} [opts]
 */
export function watchPiHostOnVps(apiBase, roomIdOrPath, onRelaunch, opts = {}) {
    const iceToken = opts.iceToken || "";
    let since = 0;
    let lastLaunchId = undefined;
    let lastNeedOffer = undefined;
    let sawLaunchId = false;
    let needOfferReady = false;
    let launchReady = false;
    let stopped = false;

    function handleHost(host) {
        if (!host) {
            return;
        }
        const need = host.needOffer === true;
        const launchId = host.hostLaunchId ?? null;

        if (!needOfferReady) {
            needOfferReady = true;
            lastNeedOffer = need;
            if (need && opts.onPiWaiting) {
                opts.onPiWaiting({ hostLaunchId: launchId, initial: true });
            }
        } else if (need && lastNeedOffer !== true) {
            onRelaunch({ why: "needOffer", needOffer: true, hostLaunchId: launchId });
            opts.onPiWaiting?.({ hostLaunchId: launchId, initial: false });
        }
        lastNeedOffer = need;

        if (!launchReady) {
            launchReady = true;
            lastLaunchId = launchId;
            if (launchId != null) {
                sawLaunchId = true;
            }
        } else if (sawLaunchId && launchId != null && launchId !== lastLaunchId) {
            onRelaunch({ why: "hostLaunchId", needOffer: true, hostLaunchId: launchId });
        }
        if (launchId != null) {
            sawLaunchId = true;
        }
        lastLaunchId = launchId;
    }

    async function loop() {
        while (!stopped) {
            try {
                const ev = await signalWaitEvents(apiBase, roomIdOrPath, since, 25, iceToken);
                since = ev.seq ?? since;
                handleHost(ev.host);
                if (ev.host && opts.onHostUpdate) {
                    opts.onHostUpdate(ev.host);
                }
                if (ev.answer) {
                    /* answer handled by connect flow */
                }
            } catch (_) {
                await new Promise((r) => setTimeout(r, 1500));
            }
        }
    }

    void loop();

    return () => {
        stopped = true;
    };
}
