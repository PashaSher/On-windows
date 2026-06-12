#!/usr/bin/env python3
"""Deploy Pi microphone → browser audio on Raspberry Pi WebRTC host."""
from __future__ import annotations

import sys
from pathlib import Path

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST, USER, PASSWORD = "rpi5-ar", "pavel", "2214"
PROJECT = "/home/pavel/projects/Mobile_Raspberry_5-"
REPO = Path(__file__).resolve().parent.parent


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


def patch_webrtc_host_audio(txt: str) -> str:
    if "webrtc_audio import attach_microphone_to_pc" in txt:
        return txt

    if "self._video_track: H264CameraTrack | None = None" in txt:
        txt = txt.replace(
            "self._video_track: H264CameraTrack | None = None",
            "self._video_track: H264CameraTrack | None = None\n        self._audio_player = None",
            1,
        )
    elif "self._video_track = None" in txt and "self._audio_player" not in txt:
        txt = txt.replace(
            "        self._video_track: H264CameraTrack | None = None\n        self._pc:",
            "        self._video_track: H264CameraTrack | None = None\n        self._audio_player = None\n        self._pc:",
            1,
        )

    anchor = (
        '        log.info(\n'
        '            "WebRTC: H.264-трек привязан к трансceiver (replaceTrack) перед createAnswer"\n'
        '        )\n'
    )
    alt_anchor = (
        '        log.info(\n'
        '            "WebRTC: H.264-'
    )
    insert = (
        '        from rpi_tools.webrtc_audio import attach_microphone_to_pc, stop_audio_player\n'
        "\n"
        "        self._audio_player = attach_microphone_to_pc(self._pc)\n"
        "\n"
    )

    if anchor in txt:
        txt = txt.replace(anchor, anchor + insert, 1)
    elif "attach_microphone_to_pc(self._pc)" not in txt:
        marker = "        answer = await self._pc.createAnswer()"
        if marker in txt:
            txt = txt.replace(marker, insert + marker, 1)
        else:
            raise RuntimeError("webrtc_host.py: cannot find insertion point for Pi mic")

    cleanup_old = "        if self._video_track:\n            self._video_track.stop()\n            self._video_track = None"
    cleanup_new = (
        "        from rpi_tools.webrtc_audio import stop_audio_player\n"
        "\n"
        "        stop_audio_player(self._audio_player)\n"
        "        self._audio_player = None\n"
        "        if self._video_track:\n"
        "            self._video_track.stop()\n"
        "            self._video_track = None"
    )
    if cleanup_old in txt and "stop_audio_player(self._audio_player)" not in txt:
        txt = txt.replace(cleanup_old, cleanup_new, 1)

    return txt


def main() -> None:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=20)
    sftp = client.open_sftp()

    audio_src = REPO / "scripts" / "pi_patch" / "webrtc_audio.py"
    remote_audio = f"{PROJECT}/rpi_tools/webrtc_audio.py"
    write_remote(sftp, remote_audio, audio_src.read_text(encoding="utf-8"))
    print("uploaded", remote_audio)

    host_path = f"{PROJECT}/rpi_tools/webrtc_host.py"
    host_txt = patch_webrtc_host_audio(read_remote(sftp, host_path))
    write_remote(sftp, host_path, host_txt)
    print("patched", host_path)

    code, out, err = run(client, "sudo systemctl restart webrtc-vps.service 2>&1; sleep 4; "
                             "systemctl is-active webrtc-vps.service; "
                             "journalctl -u webrtc-vps -n 25 --no-pager 2>&1")
    print(out)
    if err:
        print(err, file=sys.stderr)

    sftp.close()
    client.close()
    print("done — Connect на http://116.203.148.254/cam, в логе браузера: Received remote track: audio")


if __name__ == "__main__":
    main()
