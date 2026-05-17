#!/usr/bin/env python3
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "webrtc-client.html"
t = p.read_text(encoding="utf-8")

STAGE_LINES = [
    '        <div class="stage">',
    '            <div class="video-container">',
    '                <video id="remoteVideo" autoplay playsinline muted></video>',
    '                <div class="video-overlay" id="videoInfo">No stream</motion>',
    '            </div>',
    '',
    '            <div class="control-dock" id="controlDock">',
    '                <div class="joystick-row">',
    '                    <div class="joystick-wrap">',
    '                        <span class="joystick-label">Езда</span>',
    '                        <motion id="joyDrive" aria-label="Джойстик езды"></div>',
    '                    </div>',
    '                    <div class="joystick-mid">',
    '                        <button type="button" class="btn-round btn-stop-round" id="btnStopAll" title="Стоп">■</button>',
    '                        <button type="button" class="btn-round btn-home-round" id="btnHome" title="Home">H</button>',
    '                    </div>',
    '                    <div class="joystick-wrap">',
    '                        <span class="joystick-label">Камера</span>',
    '                        <div id="joyTurret" aria-label="Джойстик камеры и башни"></div>',
    '                    </div>',
    '                </div>',
    '                <div class="camera-toolbar">',
    '                    <button type="button" class="btn-chip btn-chip--zoom" data-zoom="out">−</button>',
    '                    <button type="button" class="btn-chip btn-chip--zoom" data-zoom="reset">1×</button>',
    '                    <button type="button" class="btn-chip btn-chip--zoom" data-zoom="in">+</button>',
    '                    <button type="button" class="btn-chip" data-preset="auto">Auto</button>',
    '                    <button type="button" class="btn-chip" data-preset="day">Day</button>',
    '                    <button type="button" class="btn-chip" data-preset="night">Night</button>',
    '                    <button type="button" class="btn-chip" data-preset="sport">Sport</button>',
    '                </div>',
    '            </div>',
    '        </div>',
    '',
    '        <div class="sidebar">',
]
# fix typos in list - lines 4 and 10
STAGE_LINES[3] = '                <div class="video-overlay" id="videoInfo">No stream</div>'
STAGE_LINES[4] = '            </div>'
STAGE_LINES[9] = '                        <motion id="joyDrive" aria-label="Джойстик езды"></div>'
STAGE_LINES[9] = '                        <div id="joyDrive" aria-label="Джойстик езды"></motion>'
STAGE_LINES[9] = '                        <div id="joyDrive" aria-label="Джойстик езды"></div>'

STAGE = "\n".join(STAGE_LINES)

old = """        <div class="video-container">
            <video id="remoteVideo" autoplay playsinline muted></video>
            <div class="video-overlay" id="videoInfo">No stream</div>
        </div>

        <div class="sidebar">"""

if old not in t:
    raise SystemExit("video block not found")
t = t.replace(old, STAGE, 1)

drive_start = t.find("            <div>\n                <h3>Drive WASD")
drive_end = t.find("            <motion>\n                <h3>Custom Command")
if drive_end < 0:
    drive_end = t.find("            <div>\n                <h3>Custom Command")
if drive_start < 0 or drive_end < 0:
    raise SystemExit("drive section not found")
hint = '            <p style="font-size:0.72rem;color:#888;margin:8px 0;">Джойстики под видео · WASD и стрелки на клавиатуре тоже работают.</p>\n\n'
t = t[:drive_start] + hint + t[drive_end:]

t = t.replace(
    "            <div>\n                <h3>Room &amp; ICE (cloud)</h3>",
    "            <details class=\"settings-fold\">\n                <summary>Room &amp; ICE</summary>\n                <div>\n                <h3 style=\"display:none;\">Room &amp; ICE (cloud)</h3>",
    1,
)

t = t.replace(
    '                <p style="font-size:0.68rem;color:#666;margin-top:8px;">Одна ссылка: <code>http://116.203.148.254/cam</code> (всё на VPS, без Firebase).</p>\n            </div>\n\n            <p style="font-size:0.72rem;color:#888;margin:8px 0;">Джойстики',
    '                <p style="font-size:0.68rem;color:#666;margin-top:8px;">Одна ссылка: <code>http://116.203.148.254/cam</code> (всё на VPS, без Firebase).</p>\n                </div>\n            </details>\n\n            <p style="font-size:0.72rem;color:#888;margin:8px 0;">Джойстики',
    1,
)

p.write_text(t, encoding="utf-8")
print("ok")
