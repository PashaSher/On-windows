#!/usr/bin/env python3
"""Fetch Pi WebRTC-related files for inspection."""
import paramiko
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST, USER, PASSWORD = "rpi5-ar", "pavel", "2214"
PROJECT = "/home/pavel/projects/Mobile_Raspberry_5-"
OUT = Path(__file__).resolve().parent.parent / "_pi_remote"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASSWORD, timeout=20)

stdin, stdout, stderr = client.exec_command(
    f"find {PROJECT} -name '*.py' -exec grep -l -i firebase {{}} \\; 2>/dev/null; "
    f"find {PROJECT} -name '*.py' -exec grep -l webrtc {{}} \\; 2>/dev/null",
    timeout=60,
)
paths = sorted(set(stdout.read().decode().splitlines()))
print("files:", len(paths))
for p in paths:
    print(p)

OUT.mkdir(exist_ok=True)
sftp = client.open_sftp()
for remote in paths[:30]:
    rel = remote.replace(PROJECT, "").lstrip("/")
    local = OUT / rel.replace("/", "_")
    local.parent.mkdir(parents=True, exist_ok=True)
    sftp.get(remote, str(local))
    print("saved", local.name)

# always fetch stream_camera and env/service
for remote in [
    f"{PROJECT}/stream_camera.py",
    "/etc/systemd/system/camstream.service",
    f"{PROJECT}/.env",
    f"{PROJECT}/config/webrtc.env",
]:
    try:
        name = remote.replace("/", "_").strip("_")
        sftp.get(remote, str(OUT / name))
        print("saved", name)
    except OSError as e:
        print("skip", remote, e)

sftp.close()
client.close()
