/**
 * WASD / стрелки / IJKL → JSON в Data Channel (romeo).
 */

export const ROME_TURRET_LR_SWAP = true;

export function wireTurretDirForPi(sem) {
    if (!ROME_TURRET_LR_SWAP) {
        return sem;
    }
    const m = { left: "right", right: "left", up: "up", down: "down" };
    return m[sem] ?? sem;
}

export function romeoDriveJson(dirUi) {
    const dir = dirUi === "backward" ? "back" : dirUi === "back" ? "back" : dirUi;
    return JSON.stringify({ action: "drive", dir });
}

export function romeoTurretSmoothJson(dir, rate) {
    const payload = { action: "turret_smooth", dir: wireTurretDirForPi(dir) };
    if (typeof rate === "number" && rate > 0) {
        payload.v = rate;
    }
    return JSON.stringify(payload);
}

export function romeoTurretVelJson(pan, tilt) {
    return JSON.stringify({ action: "turret_vel", pan, tilt });
}

export function romeoTurretStopJson() {
    return JSON.stringify({ action: "turret_stop" });
}

export function romeoHomeJson() {
    return JSON.stringify({ action: "home" });
}

export function cameraZoomJson(op) {
    return JSON.stringify({ action: "camera_zoom", op });
}

export function cameraPresetJson(preset) {
    return JSON.stringify({ action: "camera_preset", preset });
}

export function cameraStatusJson() {
    return JSON.stringify({ action: "camera_status" });
}

/**
 * @param {(cmd: string) => void} sendCommand
 * @returns {() => void} detach listeners
 */
export function attachBrowserControlKeys(sendCommand) {
    const DRIVE_CODES = new Set(["KeyW", "KeyA", "KeyS", "KeyD"]);
    const TURRET_CODES = new Set([
        "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
        "KeyI", "KeyJ", "KeyK", "KeyL",
    ]);
    const HOME_CODES = new Set(["KeyH", "Home"]);

    const activeDriveKeys = new Set();
    const activeTurretKeys = new Set();
    const activeHomeKeys = new Set();
    let lastDriveSent = undefined;
    let lastTurretSent = undefined;

    function currentDriveDirFromKeys() {
        const order = [
            ["KeyW", "forward"],
            ["KeyS", "backward"],
            ["KeyA", "left"],
            ["KeyD", "right"],
        ];
        for (const [code, dir] of order) {
            if (activeDriveKeys.has(code)) {
                return dir;
            }
        }
        return null;
    }

    function currentTurretDirFromKeys() {
        const order = [
            ["ArrowUp", "up"], ["KeyI", "up"],
            ["ArrowDown", "down"], ["KeyK", "down"],
            ["ArrowLeft", "left"], ["KeyJ", "left"],
            ["ArrowRight", "right"], ["KeyL", "right"],
        ];
        for (const [code, dir] of order) {
            if (activeTurretKeys.has(code)) {
                return dir;
            }
        }
        return null;
    }

    function syncDriveFromKeyboard() {
        const d = currentDriveDirFromKeys();
        if (d === lastDriveSent) {
            return;
        }
        lastDriveSent = d;
        sendCommand(d ? romeoDriveJson(d) : romeoDriveJson("stop"));
    }

    function syncTurretFromKeyboard() {
        const t = currentTurretDirFromKeys();
        if (t === lastTurretSent) {
            return;
        }
        lastTurretSent = t;
        sendCommand(t ? romeoTurretSmoothJson(t) : romeoTurretStopJson());
    }

    function onKeyDown(e) {
        if (e.target.tagName === "INPUT") {
            return;
        }
        const c = e.code;
        if (HOME_CODES.has(c)) {
            const wasEmpty = activeHomeKeys.size === 0;
            activeHomeKeys.add(c);
            if (wasEmpty) {
                sendCommand(romeoHomeJson());
            }
            e.preventDefault();
            return;
        }
        if (DRIVE_CODES.has(c)) {
            if (!activeDriveKeys.has(c)) {
                activeDriveKeys.add(c);
                syncDriveFromKeyboard();
            }
            e.preventDefault();
        } else if (TURRET_CODES.has(c)) {
            if (!activeTurretKeys.has(c)) {
                activeTurretKeys.add(c);
                syncTurretFromKeyboard();
            }
            e.preventDefault();
        }
    }

    function onKeyUp(e) {
        if (e.target.tagName === "INPUT") {
            return;
        }
        const c = e.code;
        if (HOME_CODES.has(c)) {
            activeHomeKeys.delete(c);
            e.preventDefault();
            return;
        }
        if (DRIVE_CODES.has(c)) {
            if (activeDriveKeys.has(c)) {
                activeDriveKeys.delete(c);
                syncDriveFromKeyboard();
            }
            e.preventDefault();
        } else if (TURRET_CODES.has(c)) {
            if (activeTurretKeys.has(c)) {
                activeTurretKeys.delete(c);
                syncTurretFromKeyboard();
            }
            e.preventDefault();
        }
    }

    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("keyup", onKeyUp);

    return () => {
        document.removeEventListener("keydown", onKeyDown);
        document.removeEventListener("keyup", onKeyUp);
    };
}
