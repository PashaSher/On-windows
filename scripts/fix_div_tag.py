from pathlib import Path

DIV = chr(100) + chr(105) + chr(118)
BAD = "createElement(\"" + "mo" + "tion" + "\")"
GOOD = "createElement(\"" + DIV + "\")"

p = Path(__file__).resolve().parent.parent / "examples" / "webrtc_touch_joysticks.js"
t = p.read_text(encoding="utf-8")
t = t.replace(BAD, GOOD)
p.write_text(t, encoding="utf-8")
print("ok", t.count(BAD), "left")
