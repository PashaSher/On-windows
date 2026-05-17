from pathlib import Path

for rel in ("webrtc-client.html", "deploy/www/webrtc-client.html"):
    p = Path(r"c:\projects\On-windows") / rel
    if not p.is_file():
        continue
    t = p.read_text(encoding="utf-8")
    t2 = t.replace("<motion ", "<div ").replace("</motion>", "</div>")
    if t2 != t:
        p.write_text(t2, encoding="utf-8")
        print("fixed", rel)
