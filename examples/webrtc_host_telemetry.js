/**
 * Телеметрия Pi из VPS signaling (host.batteryV, wifi*, telemetryAt).
 */

function num(v) {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
}

function pickTelemetryAt(host) {
    if (!host || typeof host !== "object") {
        return null;
    }
    const flat = num(host.telemetryAt);
    if (flat != null) {
        return flat;
    }
    const tel = host.telemetry;
    if (tel && typeof tel === "object") {
        return num(tel.at_ms) ?? num(tel.atMs) ?? null;
    }
    return null;
}

function pickBatteryV(host) {
    const flat = num(host?.batteryV);
    if (flat != null) {
        return flat;
    }
    const b = host?.telemetry?.battery;
    if (b && typeof b === "object") {
        return (
            num(b.voltage_v) ??
            num(b.voltageV) ??
            num(b.v) ??
            num(b.volts) ??
            null
        );
    }
    return null;
}

function pickWifi(host) {
    const telWifi = host?.telemetry?.wifi;
    const w = telWifi && typeof telWifi === "object" ? telWifi : {};
    const connected =
        host?.wifiConnected === false
            ? false
            : host?.wifiConnected === true || w.connected === true
              ? true
              : host?.wifiConnected == null && w.connected == null
                ? null
                : Boolean(host?.wifiConnected ?? w.connected);

    return {
        connected,
        signal: num(host?.wifiSignal) ?? num(w.signal) ?? num(w.signal_pct) ?? null,
        ssid: String(host?.wifiSsid ?? w.ssid ?? w.SSID ?? "").trim(),
        rate: String(host?.wifiRate ?? w.rate ?? "").trim(),
    };
}

/**
 * @param {Record<string, unknown> | null | undefined} host
 */
export function formatHostTelemetry(host) {
    if (!host) {
        return {
            batteryText: "Батарея: —",
            wifiText: "Wi‑Fi: —",
            ageText: "",
            batteryLow: false,
        };
    }

    const batteryV = pickBatteryV(host);
    const wifi = pickWifi(host);
    const atMs = pickTelemetryAt(host);

    let batteryText;
    if (batteryV != null) {
        batteryText = `Батарея: ${batteryV.toFixed(2)} V`;
    } else {
        batteryText = "Батарея: —";
    }

    let wifiText;
    if (wifi.connected === false) {
        wifiText = "Wi‑Fi: нет связи";
    } else if (wifi.ssid || wifi.signal != null) {
        const pct = wifi.signal != null ? `${Math.round(wifi.signal)}%` : "—";
        wifiText = wifi.ssid ? `Wi‑Fi: ${wifi.ssid}, ${pct}` : `Wi‑Fi: ${pct}`;
    } else {
        wifiText = "Wi‑Fi: —";
    }

    let ageText = "";
    if (atMs != null) {
        const sec = Math.max(0, Math.floor((Date.now() - atMs) / 1000));
        ageText = sec < 120 ? ` · ${sec} с назад` : "";
    }

    return {
        batteryText,
        wifiText,
        ageText,
        batteryLow: batteryV != null && batteryV < 3.5,
        wifiTitle: wifi.rate || undefined,
    };
}

/**
 * @param {Record<string, unknown> | null | undefined} host
 * @param {{ battery?: HTMLElement, wifi?: HTMLElement, age?: HTMLElement }} els
 */
export function updateHostTelemetryUi(host, els) {
    if (!els?.battery && !els?.wifi && !els?.age) {
        return;
    }
    const f = formatHostTelemetry(host);
    if (els.battery) {
        els.battery.textContent = f.batteryText;
        els.battery.classList.toggle("telemetry--low", f.batteryLow);
    }
    if (els.wifi) {
        els.wifi.textContent = f.wifiText;
        if (f.wifiTitle) {
            els.wifi.title = f.wifiTitle;
        } else {
            els.wifi.removeAttribute("title");
        }
        els.wifi.classList.toggle("telemetry--offline", f.wifiText.includes("нет связи"));
    }
    if (els.age) {
        els.age.textContent = f.ageText;
    }
}
