import re
from pathlib import Path

p = Path(r"c:\projects\On-windows\webrtc-client.html")
t = p.read_text(encoding="utf-8")
t = re.sub(r"\s*<m[a-z]+ id=\"joyDrive\"[^>]*></div>\s*", "\n", t)
# ensure label before joyDrive
t = t.replace(
    '                    <div class="joystick-wrap">\n                        <motion id="joyDrive"',
    '                    <div class="joystick-wrap">\n                        <span class="joystick-label">Езда</span>\n                        <motion id="joyDrive"',
)
t = t.replace(
    '                    <motion class="joystick-wrap">\n                        <div id="joyDrive"',
    '                    <div class="joystick-wrap">\n                        <span class="joystick-label">Езда</span>\n                        <div id="joyDrive"',
)
if 'joystick-label">Езда' not in t.split("joyDrive")[0][-200:]:
    t = t.replace(
        '<div id="joyDrive" aria-label="Джойстик езды"></div>',
        '<span class="joystick-label">Езда</span>\n                        <div id="joyDrive" aria-label="Джойстик езды"></div>',
        1,
    )
p.write_text(t, encoding="utf-8")
print("done")
