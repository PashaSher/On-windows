/**
 * Единый импорт Firebase RTDB (v10 modular).
 * Все ref/push/onValue — только отсюда, иначе _checkNotDeleted между модулями.
 */

import { ref, set, onValue, push, onChildAdded, get, remove } from
    "https://www.gstatic.com/firebasejs/10.12.2/firebase-database.js";

export { ref, set, onValue, push, onChildAdded, get, remove };

/** @param {string} roomIdOrPath  pi-camera | rooms/pi-camera */
export function normalizeRoomPath(roomIdOrPath) {
    let r = String(roomIdOrPath || "").trim();
    if (!r) {
        r = "pi-camera";
    }
    if (!r.includes("/")) {
        return `rooms/${r}`;
    }
    return r;
}

/** @param {import('firebase/database').Database} db */
export function roomRef(db, roomIdOrPath) {
    return ref(db, normalizeRoomPath(roomIdOrPath));
}

/** @param {import('firebase/database').Database} db */
export function roomChildRef(db, roomIdOrPath, childKey) {
    return ref(db, `${normalizeRoomPath(roomIdOrPath)}/${childKey}`);
}
