/**
 * Следит за перезапуском WebRTC на Pi (needOffer, hostLaunchId).
 */

import { onValue, roomChildRef } from "./webrtc_firebase_rtdb.js";

/**
 * @param {import('firebase/database').Database} db
 * @param {string} roomIdOrPath
 * @param {(info: object) => void} onRelaunch
 * @param {{ onPiWaiting?: () => void }} [opts]
 */
export function watchPiHostRelaunch(db, roomIdOrPath, onRelaunch, opts = {}) {
    let lastLaunchId = undefined;
    let lastNeedOffer = undefined;
    let sawLaunchId = false;
    let needOfferReady = false;
    let launchReady = false;

    const unNeed = onValue(roomChildRef(db, roomIdOrPath, "needOffer"), (snap) => {
        const v = snap.val();
        if (!needOfferReady) {
            needOfferReady = true;
            lastNeedOffer = v;
            if (v === true && opts.onPiWaiting) {
                opts.onPiWaiting({ hostLaunchId: lastLaunchId ?? null, initial: true });
            }
            return;
        }
        if (v === true && lastNeedOffer !== true) {
            onRelaunch({ why: "needOffer", needOffer: true, hostLaunchId: lastLaunchId ?? null });
            if (opts.onPiWaiting) {
                opts.onPiWaiting({ hostLaunchId: lastLaunchId ?? null, initial: false });
            }
        }
        lastNeedOffer = v;
    });

    const unLaunch = onValue(roomChildRef(db, roomIdOrPath, "hostLaunchId"), (snap) => {
        const v = snap.val();
        if (!launchReady) {
            launchReady = true;
            lastLaunchId = v;
            if (v != null) {
                sawLaunchId = true;
            }
            return;
        }
        if (sawLaunchId && v != null && v !== lastLaunchId) {
            onRelaunch({ why: "hostLaunchId", needOffer: true, hostLaunchId: v });
        }
        if (v != null) {
            sawLaunchId = true;
        }
        lastLaunchId = v;
    });

    return () => {
        unNeed();
        unLaunch();
    };
}
