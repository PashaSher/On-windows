from pathlib import Path
p = Path(r"c:\projects\On-windows\webrtc-client.html")
t = p.read_text(encoding="utf-8")
t = t.replace(
    '<motion id="joyDrive" aria-label="Джойстик езды"></div>\n</div>',
    '<div id="joyDrive" aria-label="Джойстик езды"></motion>\n                    </div>',
)
t = t.replace(
    '<div id="joyDrive" aria-label="Джойстик езды"></div>\n</div>',
    '<div id="joyDrive" aria-label="Джойстик езды"></div>\n                    </motion>',
)
t = t.replace(
    '<div id="joyDrive" aria-label="Джойстик езды"></div>\n                    </motion>',
    '<div id="joyDrive" aria-label="Джойстик езды"></div>\n                    </div>',
)
p.write_text(t, encoding="utf-8")
print("ok")
