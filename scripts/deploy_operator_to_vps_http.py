#!/usr/bin/env python3
"""Upload deploy/www to VPS via PUT /api/operator-static/ (needs updated ice_config_server)."""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE = "http://116.203.148.254:8788"
DEFAULT_TOKEN = "698567c765668e1abf9c7456c0d89991fd65ac8c606f262e"


def upload_file(base: str, token: str, rel_path: str, data: bytes) -> None:
    url = f"{base.rstrip('/')}/api/operator-static/{rel_path.lstrip('/')}"
    req = urllib.request.Request(url, data=data, method="PUT")
    req.add_header("Authorization", f"Bearer {token}")
    ct = "text/html" if rel_path.endswith(".html") else "application/javascript"
    req.add_header("Content-Type", ct)
    with urllib.request.urlopen(req, timeout=60) as resp:
        if resp.status != 200:
            raise RuntimeError(f"{rel_path}: HTTP {resp.status}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("VPS_ICE_BASE", DEFAULT_BASE))
    ap.add_argument("--token", default=os.environ.get("ICE_CONFIG_TOKEN", DEFAULT_TOKEN))
    ap.add_argument("--www", type=Path, default=Path(__file__).resolve().parents[1] / "deploy" / "www")
    args = ap.parse_args()
    if not args.www.is_dir():
        print(f"Missing bundle: {args.www}", file=sys.stderr)
        return 1
    files = [p for p in args.www.rglob("*") if p.is_file()]
    ok = 0
    for path in sorted(files):
        rel = path.relative_to(args.www).as_posix()
        try:
            upload_file(args.base, args.token, rel, path.read_bytes())
            print(f"OK {rel}")
            ok += 1
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(
                    "Deploy API not on VPS yet (404). Upload ice_config_server.py once via SSH,\n"
                    "  or use cloudflared tunnel on Pi for public access.",
                    file=sys.stderr,
                )
                return 2
            print(f"FAIL {rel}: HTTP {e.code}", file=sys.stderr)
            return 1
        except OSError as e:
            print(f"FAIL {rel}: {e}", file=sys.stderr)
            return 1
    print(f"Uploaded {ok} files. Open http://116.203.148.254/cam")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
