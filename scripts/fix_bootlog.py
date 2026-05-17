import re
from pathlib import Path

p = Path(r"c:\projects\On-windows\webrtc-client.html")
t = p.read_text(encoding="utf-8")
t2, n = re.subn(
    r"function bootLog\(msg, level\) \{.*?el\.scrollTop = el\.scrollHeight;\s*\}",
    'function bootLog(msg) { if (msg) console.warn("[webrtc]", msg); }',
    t,
    count=1,
    flags=re.DOTALL,
)
if not n:
    raise SystemExit("bootLog not found")
p.write_text(t2, encoding="utf-8")
print("ok")
