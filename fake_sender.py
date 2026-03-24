#!/usr/bin/env python3
"""
Локальная проверка receive_stream.py без Raspberry Pi.
Запуск: сначала в другом терминале — python receive_stream.py listen --port 5000
        затем — python fake_sender.py
"""

from __future__ import annotations

import argparse
import socket
import struct
import time

import cv2
import numpy as np


def main() -> None:
    p = argparse.ArgumentParser(description="Send test MJPEG stream (same TCP framing as Pi)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--fps", type=float, default=15.0)
    args = p.parse_args()

    delay = max(0.001, 1.0 / args.fps)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    print(f"Connecting to {args.host}:{args.port} ...")
    sock.connect((args.host, args.port))
    print("Sending frames (Ctrl+C stop)")
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 80]
    n = 0
    try:
        while True:
            img = np.zeros((480, 640, 3), dtype=np.uint8)
            img[:, :] = (40, 40, 120)
            t = time.strftime("%H:%M:%S")
            cv2.putText(
                img,
                f"fake_sender n={n} {t}",
                (24, 120),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (0, 255, 200),
                2,
                cv2.LINE_AA,
            )
            ok, buf = cv2.imencode(".jpg", img, encode_params)
            if not ok:
                continue
            payload = buf.tobytes()
            sock.sendall(struct.pack(">I", len(payload)) + payload)
            n += 1
            time.sleep(delay)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
