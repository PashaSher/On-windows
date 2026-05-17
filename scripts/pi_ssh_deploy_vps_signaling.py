#!/usr/bin/env python3
"""Deploy VPS WebRTC signaling to Raspberry Pi (replaces Firebase for webrtc)."""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST, USER, PASSWORD = "rpi5-ar", "pavel", "2214"
PROJECT = "/home/pavel/projects/Mobile_Raspberry_5-"
REPO = Path(__file__).resolve().parent.parent

ICE_TOKEN = ""
ICE_URL = "http://116.203.148.254/api/ice"
SIGNAL_URL = "http://116.203.148.254/api/signal"
ROOM = "pi-camera"

env_file = REPO / "config" / "webrtc.ice.local.env"
if env_file.is_file():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if k == "WEBRTC_ICE_CONFIG_TOKEN":
            ICE_TOKEN = v
        elif k == "WEBRTC_ICE_CONFIG_URL":
            ICE_URL = v.replace(":8788", "").replace("/api/ice", "/api/ice")
            if ":8788" in v:
                ICE_URL = "http://116.203.148.254/api/ice"
        elif k == "WEBRTC_ROOM":
            ROOM = v

if not ICE_TOKEN:
    print("ERROR: WEBRTC_ICE_CONFIG_TOKEN missing in config/webrtc.ice.local.env", file=sys.stderr)
    sys.exit(1)


def run(client: paramiko.SSHClient, cmd: str) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd, timeout=120)
    code = stdout.channel.recv_exit_status()
    return (
        code,
        stdout.read().decode("utf-8", errors="replace"),
        stderr.read().decode("utf-8", errors="replace"),
    )


def read_remote(sftp: paramiko.SFTPClient, path: str) -> str:
    with sftp.file(path, "r") as f:
        return f.read().decode("utf-8")


def write_remote(sftp: paramiko.SFTPClient, path: str, text: str) -> None:
    with sftp.file(path, "w") as f:
        f.write(text)


def patch_webrtc_host(txt: str) -> str:
    if "webrtc_vps_signaling" in txt:
        return txt
    txt = txt.replace(
        "import asyncio\nimport json",
        "import asyncio\nimport os\nimport json",
        1,
    )
    txt = txt.replace(
        "from rpi_tools.webrtc_signaling import FirebaseSignaling, init_firebase",
        "from rpi_tools.webrtc_vps_signaling import VpsSignaling, make_signaling",
    )
    txt = txt.replace("signaling: FirebaseSignaling,", "signaling: VpsSignaling,")
    txt = txt.replace(
        "self._signaling: FirebaseSignaling | None = None",
        "self._signaling: VpsSignaling | None = None",
    )
    txt = txt.replace(
        """        firebase_cred: str,
        firebase_db_url: str,
        room_id: str,""",
        """        room_id: str,
        signal_url: str | None = None,
        ice_token: str | None = None,""",
    )
    txt = txt.replace(
        """        self._firebase_cred = firebase_cred
        self._firebase_db_url = firebase_db_url
        self._room_id = room_id""",
        """        self._room_id = room_id
        self._signal_url = (signal_url or os.environ.get("WEBRTC_SIGNAL_URL", "")).strip() or None
        self._ice_token = ice_token or os.environ.get("ICE_CONFIG_TOKEN")""",
    )
    txt = txt.replace(
        """        init_firebase(self._firebase_cred, self._firebase_db_url)
        log.info(
            "WebRTC: signaling в Firebase только в /rooms/%s/ … "
            "Имя комнаты ДОЛЖНО быть тем же во всём коде браузера (иначе offer уйдёт «в другую» комнату, answer не вернётся).",
            self._room_id,
        )""",
        """        log.info(
            "WebRTC: signaling на VPS %s, комната %s",
            self._signal_url,
            self._room_id,
        )""",
    )
    txt = txt.replace(
        "        self._signaling = FirebaseSignaling(self._room_id)",
        """        self._signaling = make_signaling(
            self._room_id,
            signal_url=self._signal_url,
            ice_token=self._ice_token,
        )""",
    )
    txt = txt.replace(
        "            self._signaling = FirebaseSignaling(self._room_id)",
        """            self._signaling = make_signaling(
                self._room_id,
                signal_url=self._signal_url,
                ice_token=self._ice_token,
            )""",
    )
    txt = txt.replace(
        '"WebRTC: SDP → Firebase calleeCandidates: %d%s"',
        '"WebRTC: SDP → VPS calleeCandidates: %d%s"',
    )
    txt = txt.replace(
        '"WebRTC: skip ICE typ=%s для Firebase (режим VPS-only)"',
        '"WebRTC: skip ICE typ=%s (режим VPS-only)"',
    )
    txt = txt.replace(
        '"WebRTC: local ICE typ=%s -> Firebase calleeCandidates (%.80s…)"',
        '"WebRTC: local ICE typ=%s -> VPS calleeCandidates (%.80s…)"',
    )
    txt = txt.replace(
        """    firebase_cred: str,
    firebase_db_url: str,
    room_id: str,""",
        """    room_id: str,
    signal_url: str | None = None,
    ice_token: str | None = None,""",
    )
    txt = txt.replace(
        """        firebase_cred=firebase_cred,
        firebase_db_url=firebase_db_url,
        room_id=room_id,""",
        """        room_id=room_id,
        signal_url=signal_url,
        ice_token=ice_token,""",
    )
    return txt


def patch_cli(txt: str) -> str:
    if '"--signal-url"' in txt and "Firebase signaling" not in txt.split("p_webrtc =")[1][:200]:
        pass
    old_parser = """    p_webrtc = sub.add_parser(
        "webrtc",
        help="WebRTC Host: H.264 видео + Data Channel управление через Firebase signaling",
    )
    p_webrtc.add_argument(
        "--firebase-cred",
        required=True,
        metavar="PATH",
        type=_firebase_cred_existing_path,
        help="Путь к serviceAccountKey.json от Firebase",
    )
    p_webrtc.add_argument(
        "--firebase-db-url",
        required=True,
        metavar="URL",
        type=_firebase_rtdb_url,
        help=(
            "URL Firebase Realtime Database, "
            "например https://<id>-default-rtdb.firebaseio.com (подобрать: команда firebase-probe)"
        ),
    )"""
    new_parser = """    p_webrtc = sub.add_parser(
        "webrtc",
        help="WebRTC Host: H.264 видео + Data Channel (signaling на VPS, без Firebase)",
    )
    p_webrtc.add_argument(
        "--signal-url",
        default=os.environ.get("WEBRTC_SIGNAL_URL", ""),
        metavar="URL",
        help="VPS signaling API, напр. http://116.203.148.254/api/signal",
    )"""
    if old_parser in txt:
        txt = txt.replace(old_parser, new_parser, 1)

    txt = txt.replace(
        """                from rpi_tools.webrtc_signaling import FirebaseSignaling, init_firebase

                init_firebase(args.firebase_cred, args.firebase_db_url)
                await FirebaseSignaling(args.room).reset_room_for_host_launch(""",
        """                from rpi_tools.webrtc_vps_signaling import VpsSignaling

                sig = VpsSignaling(
                    args.room,
                    api_base=(getattr(args, "signal_url", None) or os.environ.get("WEBRTC_SIGNAL_URL", "")).strip(),
                    ice_token=(getattr(args, "ice_config_token", None) or os.environ.get("ICE_CONFIG_TOKEN")),
                )
                await sig.reset_room_for_host_launch""",
    )
    txt = txt.replace(
        """            log.info(
                "webrtc --room-only: ок. В Firebase Console откройте Realtime Database и узел rooms/%s",
                args.room,
            )""",
        """            log.info(
                "webrtc --room-only: ок. VPS signaling, комната %s (откройте http://116.203.148.254/cam)",
                args.room,
            )""",
    )

    old_run = """            asyncio.run(run_webrtc_host(
                firebase_cred=args.firebase_cred,
                firebase_db_url=args.firebase_db_url,
                room_id=args.room,"""
    new_run = """            sig_url = (getattr(args, "signal_url", None) or os.environ.get("WEBRTC_SIGNAL_URL", "")).strip()
            if not sig_url:
                print(
                    "webrtc: задайте WEBRTC_SIGNAL_URL (config/webrtc.vps.env) или --signal-url",
                    file=sys.stderr,
                )
                return 2
            asyncio.run(run_webrtc_host(
                signal_url=sig_url,
                ice_token=(args.ice_config_token or "").strip() or None,
                room_id=args.room,"""
    if old_run in txt:
        txt = txt.replace(old_run, new_run, 1)

    return txt


def main() -> None:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=20)
    sftp = client.open_sftp()

    src = REPO / "scripts" / "pi_patch" / "webrtc_vps_signaling.py"
    remote_vps = f"{PROJECT}/rpi_tools/webrtc_vps_signaling.py"
    write_remote(sftp, remote_vps, src.read_text(encoding="utf-8"))
    print("uploaded", remote_vps)

    host_path = f"{PROJECT}/rpi_tools/webrtc_host.py"
    host_txt = patch_webrtc_host(read_remote(sftp, host_path))
    write_remote(sftp, host_path, host_txt)
    print("patched", host_path)

    cli_path = f"{PROJECT}/rpi_tools/cli.py"
    cli_txt = patch_cli(read_remote(sftp, cli_path))
    write_remote(sftp, cli_path, cli_txt)
    print("patched", cli_path)

    env_content = f"""# VPS WebRTC (без Firebase)
WEBRTC_SIGNAL_URL={SIGNAL_URL}
WEBRTC_ROOM={ROOM}
ICE_CONFIG_URL={ICE_URL}
ICE_CONFIG_TOKEN={ICE_TOKEN}
"""
    env_remote = f"{PROJECT}/config/webrtc.vps.env"
    try:
        sftp.mkdir(f"{PROJECT}/config")
    except OSError:
        pass
    write_remote(sftp, env_remote, env_content)
    print("wrote", env_remote)

    svc = f"""[Unit]
Description=WebRTC camera (VPS signaling, room {ROOM})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pavel
Group=pavel
WorkingDirectory={PROJECT}
EnvironmentFile={env_remote}
ExecStart={PROJECT}/.venv/bin/python stream_camera.py webrtc --room {ROOM} --ice-vps-only
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
    b64 = base64.b64encode(svc.encode()).decode()
    code, out, err = run(
        client,
        f"echo {b64} | base64 -d | sudo tee /etc/systemd/system/webrtc-vps.service > /dev/null "
        "&& sudo systemctl daemon-reload",
    )
    print("systemd:", code, err.strip() or "ok")

    code, out, err = run(
        client,
        "sudo systemctl enable --now webrtc-vps.service 2>&1; sleep 3; "
        "systemctl is-active webrtc-vps.service; "
        "journalctl -u webrtc-vps -n 20 --no-pager 2>&1",
    )
    print(out)
    if err:
        print(err, file=sys.stderr)

    # quick VPS reachability from Pi
    code, out, _ = run(
        client,
        f"curl -s -o /dev/null -w '%{{http_code}}' -H 'Authorization: Bearer {ICE_TOKEN}' "
        f"{SIGNAL_URL}/rooms/{ROOM}/host 2>/dev/null || echo fail",
    )
    print("VPS host GET (expect 200):", out.strip())

    sftp.close()
    client.close()
    print("done — откройте http://116.203.148.254/cam")


if __name__ == "__main__":
    main()
