/**
 * Виртуальные джойстики для телефона (pointer/touch).
 * @param {HTMLElement} root
 * @param {{ mode: 'drive'|'turret', onDir: (dir: string|null) => void, label?: string }} opts
 */
export function mountVirtualJoystick(root, opts) {
    const { mode, onDir } = opts;
    const deadzone = opts.deadzone ?? 0.22;
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
    function emit(dir) {
        const d = dir || null;
        if (d === lastDir) {
            return;
        }
        if (emitTimer) {
            clearTimeout(emitTimer);
        }
        emitTimer = setTimeout(() => {
            emitTimer = null;
            if (d === lastDir) {
                return;
            }
            lastDir = d;
            onDir(d);
        }, 55);
    }

    function moveKnob(clientX, clientY) {
        const { cx, cy, radius } = center();
        let dx = clientX - cx;
        let dy = clientY - cy;
        const mag = Math.hypot(dx, dy);
        if (mag > radius && mag > 0) {
            dx = (dx / mag) * radius;
            dy = (dy / mag) * radius;
        }
        knob.style.transform = `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px))`;
        const nx = dx / radius;
        const ny = dy / radius;
        emit(vecToDir(nx, ny));
    }

    function resetKnob() {
        knob.style.transform = "translate(-50%, -50%)";
        emit(null);
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

    return () => {
        root.removeEventListener("pointerdown", onPointerDown);
        root.removeEventListener("pointermove", onPointerMove);
        root.removeEventListener("pointerup", onPointerUp);
        root.removeEventListener("pointercancel", onPointerUp);
        resetKnob();
    };
}
