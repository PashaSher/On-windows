#!/usr/bin/env python3
import paramiko
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HOST = "rpi5-ar"
USER = "pavel"
PASSWORD = "2214"
PROJECT = "/home/pavel/projects/Mobile_Raspberry_5-"


def run(cmd: str) -> str:
    stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    if err.strip():
        print(err, file=sys.stderr)
    return out


client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASSWORD, timeout=20)
print(run(f"hostname; ls -la {PROJECT}/stream_camera.py 2>&1"))
print("=== firebase grep ===")
print(run(f"grep -rn -i firebase {PROJECT} --include='*.py' 2>/dev/null | head -50"))
print("=== webrtc grep ===")
print(run(f"grep -rn webrtc {PROJECT} --include='*.py' 2>/dev/null | head -40"))
client.close()
