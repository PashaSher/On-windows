#!/usr/bin/env python3
import paramiko, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
path = sys.argv[1]
start = int(sys.argv[2]) if len(sys.argv) > 2 else 1
end = int(sys.argv[3]) if len(sys.argv) > 3 else 99999
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("rpi5-ar", username="pavel", password="2214", timeout=20)
with c.open_sftp().open(path) as f:
    lines = f.read().decode("utf-8").splitlines()
for i, line in enumerate(lines[start - 1 : end], start):
    print(f"{i}:{line}")
c.close()
