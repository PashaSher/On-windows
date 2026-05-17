import re
from pathlib import Path

p = Path(r"c:\projects\On-windows\webrtc-client.html")
t = p.read_text(encoding="utf-8")

start = t.index("<body>")
end = t.index("    <script>", start)

body = """<body>
    <header>
        <div class="header-actions">
            <button class="btn btn-primary" id="btnConnect" onclick="startConnection()">Connect</button>
            <button class="btn btn-danger" id="btnDisconnect" onclick="hangup()" disabled>Disconnect</button>
        </motion>
        <span id="status">Disconnected</span>
    </header>

    <div class="sr-only" aria-hidden="true">
        <input type="text" id="inputRoomId" value="pi-camera" tabindex="-1">
        <input type="text" id="inputIceUrl" value="" tabindex="-1">
        <input type="text" id="inputIceToken" value="" tabindex="-1">
    </div>

    <div class="main">
        <div class="stage">
            <div class="video-container">
                <video id="remoteVideo" autoplay playsinline muted></video>
            </div>
            <div class="control-dock" id="controlDock">
                <div class="joystick-row">
                    <div class="joystick-wrap">
                        <span class="joystick-label">Езда</span>
                        <div id="joyDrive" aria-label="Джойстик езды"></div>
                    </div>
                    <div class="joystick-mid">
                        <button type="button" class="btn-round btn-home-round" id="btnHome" title="Home">H</button>
                    </div>
                    <div class="joystick-wrap">
                        <span class="joystick-label">Камера</span>
                        <motion id="joyTurret" aria-label="Джойстик камеры"></div>
                    </div>
                </div>
            </div>
        </div>
    </div>

"""

# strip accidental motion tags
body = re.sub(r"</?m[a-z]+\b[^>]*>", "", body)
body = body.replace(
    """        <div class="header-actions">
            <button class="btn btn-primary" id="btnConnect" onclick="startConnection()">Connect</button>
            <button class="btn btn-danger" id="btnDisconnect" onclick="hangup()" disabled>Disconnect</button>
        
        <span id="status">Disconnected</span>""",
    """        <motion class="header-actions">
            <button class="btn btn-primary" id="btnConnect" onclick="startConnection()">Connect</button>
            <button class="btn btn-danger" id="btnDisconnect" onclick="hangup()" disabled>Disconnect</button>
        </div>
        <span id="status">Disconnected</span>""",
)
body = body.replace('<motion class="header-actions">', '<motion class="header-actions">')
body = body.replace("        \n        <span", "        </div>\n        <span", 1)
body = body.replace(
    """                    <div class="joystick-wrap">
                        <span class="joystick-label">Камера</span>
                        
                    </div>""",
    """                    <div class="joystick-wrap">
                        <span class="joystick-label">Камера</span>
                        <div id="joyTurret" aria-label="Джойстик камеры"></div>
                    </div>""",
)

# rebuild body cleanly without regex mess
body = """<body>
    <header>
        <div class="header-actions">
            <button class="btn btn-primary" id="btnConnect" onclick="startConnection()">Connect</button>
            <button class="btn btn-danger" id="btnDisconnect" onclick="hangup()" disabled>Disconnect</button>
        </div>
        <span id="status">Disconnected</span>
    </header>

    <div class="sr-only" aria-hidden="true">
        <input type="text" id="inputRoomId" value="pi-camera" tabindex="-1">
        <input type="text" id="inputIceUrl" value="" tabindex="-1">
        <input type="text" id="inputIceToken" value="" tabindex="-1">
    </div>

    <div class="main">
        <div class="stage">
            <div class="video-container">
                <video id="remoteVideo" autoplay playsinline muted></video>
            </div>
            <div class="control-dock" id="controlDock">
                <div class="joystick-row">
                    <motion class="joystick-wrap">
                        <span class="joystick-label">Езда</span>
                        <div id="joyDrive" aria-label="Джойстик езды"></div>
                    </div>
                    <div class="joystick-mid">
                        <button type="button" class="btn-round btn-home-round" id="btnHome" title="Home">H</button>
                    </div>
                    <div class="joystick-wrap">
                        <span class="joystick-label">Камера</span>
                        <div id="joyTurret" aria-label="Джойстик камеры"></div>
                    </div>
                </div>
            </div>
        </div>
    </div>

"""
body = body.replace("<motion ", "<div ")
body = body.replace("</motion>", "</div>")

t = t[:start] + body + t[end:]

t = re.sub(
    r"function log\([^)]*\)\s*\{[^}]*\}",
    "function log(_msg, _level = \"info\") {}",
    t,
    count=1,
)

t = re.sub(
    r"function bootLog\(msg, level\)\s*\{[^}]*\}",
    'function bootLog(msg) { if (msg) console.warn("[webrtc]", msg); }',
    t,
    count=1,
)

for snippet in [
    'document.getElementById("btnStopAll")?.addEventListener("click", () => stopAllMotion());\n',
    """        document.querySelectorAll(".camera-toolbar [data-zoom]").forEach((btn) => {
            btn.addEventListener("click", () => sendCameraZoom(btn.getAttribute("data-zoom")));
        });
        document.querySelectorAll(".camera-toolbar [data-preset]").forEach((btn) => {
            btn.addEventListener("click", () => sendCameraPreset(btn.getAttribute("data-preset")));
        });

""",
    """        window.sendTurretDir = sendTurretDir;
        window.sendCameraZoom = sendCameraZoom;
        window.sendCameraPreset = sendCameraPreset;
        window.stopAllMotion = stopAllMotion;

""",
    "            cameraZoomJson,\n            cameraPresetJson,\n",
]:
    t = t.replace(snippet, "")

t = t.replace('window.sendRawCmd = sendRawCmd;\n        window.saveWebrtcSettings', 'window.saveWebrtcSettings')
t = t.replace('window.saveWebrtcSettings = saveWebrtcSettings;\n        window.testIceConfig', 'window.testIceConfig')
t = t.replace("window.testIceConfig = testIceConfig;\n", "")

t = t.replace(
    '["startConnection", "testIceConfig", "hangup", "saveWebrtcSettings"]',
    '["startConnection", "hangup"]',
)

# videoInfo - remove overlay usage
t = t.replace('document.getElementById("videoInfo").textContent', '/* */')

# fix broken comment assignments - grep videoInfo
t = t.replace("/* */ = ", "")

p.write_text(t, encoding="utf-8")
print("done")
