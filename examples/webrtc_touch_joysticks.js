/**
 * Виртуальные джойстики для телефона (pointer/touch).
 * @param {HTMLElement} root
 * @param {{ mode: 'drive'|'turret', onDir: (dir: string|null) => void, label?: string }} opts
 */
export function mountVirtualJoystick(root, opts) {
    const { mode, onDir } = opts;
    const onVec = typeof opts.onVec === "function" ? opts.onVec : null;
    const analog = opts.analog ?? mode === "turret";
    const deadzone = opts.deadzone ?? (mode === "turret" ? 0.18 : 0.22);
    const moveIntervalMs = opts.moveIntervalMs ?? (mode === "turret" && analog ? 80 : 55);
    root.classList.add("joystick", `joystick--${mode}`);
    root.innerHTML = "";
    const ring = document.createElement("div");
    ring.className = "joystick-ring";
    const knob = document.createElement("div");
    knob.className = "joystick-knob";
    root.appendChild(ring);
    root.appendChild(knob);
    if (opts.label) {
        const cap = document.createElement("span");
        cap.className = "joystick-caption";
        cap.textContent = opts.label;
        root.appendChild(cap);
    }

    let active = false;
    let lastDir = null;
    let pointerId = null;

    function center() {
        const r = root.getBoundingClientRect();
        return { cx: r.left + r.width / 2, cy: r.top + r.height / 2, radius: Math.min(r.width, r.height) * 0.38 };
    }

    function vecToDir(nx, ny) {
        const mag = Math.hypot(nx, ny);
        if (mag < deadzone) {
            return null;
        }
        const ax = Math.abs(nx);
        const ay = Math.abs(ny);
        if (mode === "drive") {
            if (ay >= ax) {
                return ny < 0 ? "forward" : "backward";
            }
            return nx < 0 ? "left" : "right";
        }
        if (ay >= ax) {
            return ny < 0 ? "up" : "down";
        }
        return nx < 0 ? "left" : "right";
    }

    let emitTimer = null;
    let lastVecKey = "";
    function emit(dir) {
        const d = dir || null;
        if (analog && onVec && mode === "turret") {
            return;
        }
        if (d === lastDir) {
            return;
        }
        if (emitTimer) {
            clearTimeout(emitTimer);
            emitTimer = null;
        }
        // stop/center — сразу, без debounce (важно для отпускания на телефоне)
        if (d === null) {
            lastDir = null;
            onDir(null);
            return;
        }
        emitTimer = setTimeout(() => {
            emitTimer = null;
            if (d === lastDir) {
                return;
            }
            lastDir = d;
            onDir(d);
        }, moveIntervalMs);
    }

    let vecTimer = null;
    function emitVec(nx, ny, mag) {
        if (!onVec) {
            return;
        }
        const key = `${nx.toFixed(2)}:${ny.toFixed(2)}`;
        if (mag < deadzone) {
            if (lastVecKey !== "") {
                lastVecKey = "";
                onVec(0, 0, 0);
            }
            return;
        }
        if (key === lastVecKey) {
            return;
        }
        if (vecTimer) {
            clearTimeout(vecTimer);
        }
        vecTimer = setTimeout(() => {
            vecTimer = null;
            lastVecKey = key;
            onVec(nx, ny, mag);
        }, moveIntervalMs);
    }

    function moveKnob(clientX, clientY) {
        const { cx, cy, radius } = center();
        let dx = clientX - cx;
        let dy = clientY - cy;
        const pixMag = Math.hypot(dx, dy);
        if (pixMag > radius && pixMag > 0) {
            dx = (dx / pixMag) * radius;
            dy = (dy / pixMag) * radius;
        }
        knob.style.transform = `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px))`;
        const nx = dx / radius;
        const ny = dy / radius;
        const normMag = Math.hypot(nx, ny);
        if (analog && onVec && mode === "turret") {
            emitVec(nx, ny, normMag);
            return;
        }
        emit(vecToDir(nx, ny));
    }

    function resetKnob() {
        knob.style.transform = "translate(-50%, -50%)";
        if (emitTimer) {
            clearTimeout(emitTimer);
            emitTimer = null;
        }
        if (vecTimer) {
            clearTimeout(vecTimer);
            vecTimer = null;
        }
        if (analog && onVec && mode === "turret") {
            if (lastVecKey !== "") {
                lastVecKey = "";
                onVec(0, 0, 0);
            }
            return;
        }
        if (lastDir !== null) {
            lastDir = null;
            onDir(null);
        }
    }

    function onPointerDown(e) {
        if (active) {
            return;
        }
        active = true;
        pointerId = e.pointerId;
        root.setPointerCapture(e.pointerId);
        e.preventDefault();
        moveKnob(e.clientX, e.clientY);
    }

    function onPointerMove(e) {
        if (!active || e.pointerId !== pointerId) {
            return;
        }
        e.preventDefault();
        moveKnob(e.clientX, e.clientY);
    }

    function onPointerUp(e) {
        if (!active || e.pointerId !== pointerId) {
            return;
        }
        active = false;
        pointerId = null;
        try {
            root.releasePointerCapture(e.pointerId);
        } catch (_) {}
        resetKnob();
    }

    root.addEventListener("pointerdown", onPointerDown);
    root.addEventListener("pointermove", onPointerMove);
    root.addEventListener("pointerup", onPointerUp);
    root.addEventListener("pointercancel", onPointerUp);
    root.addEventListener("lostpointercapture", onPointerUp);

    return () => {
        root.removeEventListener("pointerdown", onPointerDown);
        root.removeEventListener("pointermove", onPointerMove);
        root.removeEventListener("pointerup", onPointerUp);
        root.removeEventListener("pointercancel", onPointerUp);
        root.removeEventListener("lostpointercapture", onPointerUp);
        resetKnob();
    };
}
