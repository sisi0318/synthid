# -*- coding: utf-8 -*-
"""Local HTTP REST API server for SynthID watermark detection."""

from __future__ import annotations

import base64
import email.parser
import json
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .auth import decode_jwt_payload
from .client import SynthIDClient
from .exceptions import SynthIDError


def parse_multipart_file(content_type: str, body: bytes) -> Tuple[bytes, str, Optional[str]]:
    """Extract the uploaded file bytes, filename, and MIME type from `multipart/form-data`."""
    header_bytes = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
    msg = email.parser.BytesParser().parsebytes(header_bytes + body)
    if not msg.is_multipart():
        raise SynthIDError("Invalid multipart/form-data payload.")

    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        disposition = part.get("Content-Disposition", "")
        if filename or 'name="file"' in disposition:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            part_mime = part.get_content_type()
            if part_mime in ("application/octet-stream", "text/plain"):
                part_mime = None
            return payload, (filename or "upload.bin"), part_mime

    raise SynthIDError("No file field found in multipart/form-data request (expected field 'file').")


class SynthIDAPIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the local SynthID REST API."""

    client: SynthIDClient

    def _send_json(self, status_code: int, data: Dict[str, Any]) -> None:
        raw = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/health"):
            claims = decode_jwt_payload(self.client.id_token)
            self._send_json(
                200,
                {
                    "status": "ok",
                    "service": "SynthID Direct API Server",
                    "user_id": self.client.user_id,
                    "push_filename": self.client.push_filename,
                    "token_exp": claims.get("exp"),
                    "token_expired": self.client.is_token_expired(),
                },
            )
            return
        self._send_json(404, {"error": f"Unknown endpoint: {parsed.path}"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/api/refresh":
            self._handle_refresh()
            return

        if parsed.path == "/api/detect":
            self._handle_detect(query)
            return

        self._send_json(404, {"error": f"Unknown endpoint: {parsed.path}"})

    def _handle_refresh(self) -> None:
        try:
            tok = self.client.ensure_valid_id_token(force_refresh=True)
            claims = decode_jwt_payload(tok)
            self._send_json(200, {"status": "ok", "user_id": self.client.user_id, "exp": claims.get("exp")})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def _handle_detect(self, query: Dict[str, List[str]]) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        content_type = self.headers.get("Content-Type", "application/json")
        fps_param = query.get("fps", [None])[0]
        fps: Optional[int] = int(fps_param) if fps_param is not None else None

        try:
            if content_type.startswith("application/json"):
                result = self._detect_from_json(body, default_fps=fps)
            elif content_type.startswith("multipart/form-data"):
                raw_bytes, filename, part_mime = parse_multipart_file(content_type, body)
                mime_override = query.get("mime_type", [part_mime])[0]
                result = self.client.detect_bytes(raw_bytes, filename=filename, mime_type=mime_override, fps=fps)
            else:
                result = self._detect_from_binary(body, content_type, query, fps=fps)

            self._send_json(200, result)
        except SynthIDError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def _detect_from_json(self, body: bytes, default_fps: Optional[int]) -> Dict[str, Any]:
        payload = json.loads(body.decode("utf-8"))
        fps = int(payload["fps"]) if payload.get("fps") is not None else default_fps
        mime_type = payload.get("mime_type")

        if "file_path" in payload:
            return self.client.detect_file(payload["file_path"], mime_type=mime_type, fps=fps)

        if "base64" in payload:
            raw_bytes = base64.b64decode(payload["base64"])
            filename = payload.get("filename", "upload.bin")
            return self.client.detect_bytes(raw_bytes, filename=filename, mime_type=mime_type, fps=fps)

        if "url" in payload:
            media_url = payload["url"]
            req = urllib.request.Request(media_url, headers={"User-Agent": self.client.user_agent})
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw_bytes = resp.read()
                resp_mime = resp.headers.get_content_type()
            filename = Path(urllib.parse.urlparse(media_url).path).name or "remote.bin"
            return self.client.detect_bytes(
                raw_bytes,
                filename=filename,
                mime_type=mime_type or resp_mime,
                fps=fps,
            )

        raise SynthIDError("JSON body must provide one of: 'file_path', 'base64', or 'url'.")

    def _detect_from_binary(
        self,
        body: bytes,
        content_type: str,
        query: Dict[str, List[str]],
        fps: Optional[int],
    ) -> Dict[str, Any]:
        if not body:
            raise SynthIDError("Empty request body.")
        filename = query.get("filename", ["upload.bin"])[0]
        mime_override = query.get("mime_type", [None])[0]
        if not mime_override and "/" in content_type and not content_type.startswith("application/"):
            mime_override = content_type.split(";")[0].strip()
        return self.client.detect_bytes(body, filename=filename, mime_type=mime_override, fps=fps)


def run_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    config_path: Union[str, Path] = "config.json",
) -> None:
    """Initialize `SynthIDClient` and start the multi-threaded HTTP REST server."""
    SynthIDAPIHandler.client = SynthIDClient.from_config(config_path)
    server = ThreadingHTTPServer((host, port), SynthIDAPIHandler)

    print(f"SynthID API Server listening on http://{host}:{port}")
    print(f"  GET  http://{host}:{port}/health")
    print(f"  POST http://{host}:{port}/api/detect")
    print(f"  POST http://{host}:{port}/api/refresh")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        server.server_close()
