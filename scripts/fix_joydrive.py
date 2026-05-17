import re
from pathlib import Path

p = Path(r"c:\projects\On-windows\webrtc-client.html")
t = p.read_text(encoding="utf-8")
t = re.sub(r"<m\w+ id=\"joyDrive\"", '<div id="joyDrive"', t)
p.write_text(t, encoding="utf-8")
print("ok")
