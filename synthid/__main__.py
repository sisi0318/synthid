# -*- coding: utf-8 -*-
"""Command-line entrypoint for the `synthid` package (`python -m synthid`)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .auth import decode_jwt_payload
from .client import SynthIDClient
from .server import run_server


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SynthID Detector CLI & API Server")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # detect
    p_detect = subparsers.add_parser("detect", help="Detect SynthID watermark on an image, audio, or video file")
    p_detect.add_argument("file", help="Path to local image, audio, or video file")
    p_detect.add_argument("--config", default="config.json", help="Path to config.json (default: config.json)")
    p_detect.add_argument("--mime", default=None, help="Explicit MIME type override (e.g. image/jpeg)")
    p_detect.add_argument("--fps", type=int, default=None, help="Video FPS (default: 30 for video)")

    # serve
    p_serve = subparsers.add_parser("serve", help="Start the local HTTP REST API server")
    p_serve.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    p_serve.add_argument("--config", default="config.json", help="Path to config.json (default: config.json)")

    # refresh
    p_refresh = subparsers.add_parser("refresh", help="Force refresh Firebase id_token using refresh_token")
    p_refresh.add_argument("--config", default="config.json", help="Path to config.json (default: config.json)")

    args = parser.parse_args(argv)

    try:
        if args.command == "serve":
            run_server(host=args.host, port=args.port, config_path=args.config)
            return 0

        client = SynthIDClient.from_config(args.config)
        if args.command == "refresh":
            new_tok = client.ensure_valid_id_token(force_refresh=True)
            claims = decode_jwt_payload(new_tok)
            print(json.dumps({"status": "ok", "user_id": client.user_id, "exp": claims.get("exp")}, indent=2))
            return 0

        if args.command == "detect":
            res = client.detect_file(args.file, mime_type=args.mime, fps=args.fps)
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
