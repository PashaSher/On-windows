#!/usr/bin/env python3
"""Deploy operator HTML + signaling fix to VPS via Raspberry Pi jump host."""
from __future__ import annotations

import sys
from pathlib import Path

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PI_HOST, PI_USER, PI_PASS = "rpi5-ar", "pavel", "2214"
VPS = "116.203.148.254"
VPS_USER = "root"
REPO = Path(__file__).resolve().parent.parent

FILES = [
    (REPO / "deploy/vps/webrtc-client_live_patched.html", "/var/www/operator/webrtc-client.html"),
    (REPO / "cloud/webrtc_signal_store.py", "/root/project/cloud/webrtc_signal_store.py"),
]


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 120) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    return code, stdout.read().decode("utf-8", errors="replace"), stderr.read().decode("utf-8", errors="replace")


def main() -> None:
    html = REPO / "deploy/vps/webrtc-client_live_patched.html"
    if not html.is_file():
        html = REPO / "webrtc-client.html"
    FILES[0] = (html, "/var/www/operator/webrtc-client.html")

    pi = paramiko.SSHClient()
    pi.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    pi.connect(PI_HOST, username=PI_USER, password=PI_PASS, timeout=25)

    # Upload to Pi /tmp then scp to VPS (Pi often has root key to VPS)
    sftp = pi.open_sftp()
    remote_tmp = []
    for local, _remote in FILES:
        name = local.name
        tmp = f"/tmp/deploy_vps_{name}"
        sftp.put(str(local), tmp)
        remote_tmp.append(tmp)
        print("pi tmp:", tmp, "<-", local)
    sftp.close()

    cmds = []
    for (local, remote), tmp in zip(FILES, remote_tmp):
        cmds.append(f"scp -o StrictHostKeyChecking=no {tmp} {VPS_USER}@{VPS}:{remote}")
    cmds.append(
        f"ssh -o StrictHostKeyChecking=no {VPS_USER}@{VPS} "
        "'systemctl restart ice-config-server 2>/dev/null; systemctl reload nginx 2>/dev/null; "
        "curl -s http://127.0.0.1/webrtc-client.html | grep -o \"Operator build: [^\\\"]*\" | head -1'"
    )
    for c in cmds:
        print(">", c)
        code, out, err = run(pi, c)
        print(out or err)
        if code != 0 and "scp" in c:
            print("FAIL", code, file=sys.stderr)
            pi.close()
            sys.exit(code)

    pi.close()
    print("done")


if __name__ == "__main__":
    main()
