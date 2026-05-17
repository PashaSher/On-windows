#!/usr/bin/env python3
"""Upload webrtc_vps_signaling.py fix to Pi and restart webrtc."""
import sys
from pathlib import Path

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST, USER, PASSWORD = "rpi5-ar", "pavel", "2214"
PROJECT = "/home/pavel/projects/Mobile_Raspberry_5-"
SRC = Path(__file__).resolve().parent / "pi_patch" / "webrtc_vps_signaling.py"
REMOTE = f"{PROJECT}/rpi_tools/webrtc_vps_signaling.py"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASSWORD, timeout=20)
sftp = c.open_sftp()
with sftp.file(REMOTE, "w") as f:
    f.write(SRC.read_text(encoding="utf-8"))
sftp.close()
print("uploaded", REMOTE)

_, stdout, _ = c.exec_command(
    "sudo systemctl restart webrtc-vps.service 2>&1; sleep 2; systemctl is-active webrtc-vps.service",
    timeout=30,
)
print(stdout.read().decode())
c.close()
