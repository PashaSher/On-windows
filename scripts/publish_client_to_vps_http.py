#!/usr/bin/env python3
"""Publish webrtc-client.html to VPS via HTTP PUT (port 8788 operator-static API)."""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE = "http://116.203.148.254:8788"
DEFAULT_TOKEN = "698567c765668e1abf9c7456c0d89991fd65ac8c606f262e"
REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("VPS_ICE_BASE", DEFAULT_BASE))
    ap.add_argument("--token", default=os.environ.get("ICE_CONFIG_TOKEN", DEFAULT_TOKEN))
    ap.add_argument("--file", type=Path, default=REPO / "webrtc-client.html")
    args = ap.parse_args()
    if not args.file.is_file():
        print(f"Missing: {args.file}", file=sys.stderr)
        return 1
    data = args.file.read_bytes()
    url = f"{args.base.rstrip('/')}/api/operator-static/webrtc-client.html"
    req = urllib.request.Request(url, data=data, method="PUT")
    req.add_header("Authorization", f"Bearer {args.token}")
    req.add_header("Content-Type", "text/html")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            print(body)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}", file=sys.stderr)
        return 1
    except OSError as e:
        print(str(e), file=sys.stderr)
        return 1
    build = ""
    for line in data.decode("utf-8", errors="replace").splitlines():
        if "duplex-audio-fix-v" in line or "Operator build:" in line:
            if "Operator build:" in line:
                build = line.strip()
                break
    print(f"Uploaded {len(data)} bytes to {url}")
    if build:
        print(build)
    print("8788:", f"{args.base.rsplit(':', 1)[0]}:8788/webrtc-client.html?room=pi-camera&autostart=1")
    print("Note: /cam on :80 uses nginx static until symlink or nginx proxy is applied on VPS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
