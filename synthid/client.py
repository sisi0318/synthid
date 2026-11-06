# -*- coding: utf-8 -*-
"""Core HTTP client for SynthID Detector Scotty upload and `batchexecute` RPC."""

from __future__ import annotations

import json
import mimetypes
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .auth import (
    decode_jwt_payload,
    is_jwt_expired,
    normalize_bg_token,
    normalize_push_filename,
)
from .constants import (
    BATCHEXECUTE_URL,
    DEFAULT_FIREBASE_API_KEY,
    DEFAULT_PUSH_ID,
    DEFAULT_SCOTTY_BASIC_AUTH,
    DEFAULT_USER_AGENT,
    EXTENSION_MIME_MAP,
    MAX_FILE_SIZE_BYTES,
    SCOTTY_UPLOAD_URL,
    SECURETOKEN_URL,
)
from .exceptions import SynthIDError
from .parser import parse_batchexecute_response


def guess_mime_type(filename: str, explicit_mime: Optional[str] = None) -> str:
    """Resolve supported MIME type from file extension or explicit override."""
    if explicit_mime:
        return explicit_mime
    ext = Path(filename).suffix.lower()
    if ext in EXTENSION_MIME_MAP:
        return EXTENSION_MIME_MAP[ext]
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def get_modality(mime_type: str) -> str:
    """Return 'image', 'audio', or 'video' based on MIME prefix."""
    prefix = mime_type.split("/")[0].lower()
    if prefix in ("image", "audio", "video"):
        return prefix
    raise SynthIDError(f"Unsupported media MIME type: {mime_type}")


class SynthIDClient:
    """Thread-safe direct HTTP client for https://synthid.com/ watermark detection."""

    def __init__(
        self,
        refresh_token: str,
        push_filename: str,
        bg_token: Union[str, List[Any]],
        id_token: str = "",
        user_id: str = "",
        firebase_api_key: str = DEFAULT_FIREBASE_API_KEY,
        push_id: str = DEFAULT_PUSH_ID,
        scotty_basic_auth: str = DEFAULT_SCOTTY_BASIC_AUTH,
        config_path: Optional[Union[str, Path]] = None,
        timeout: int = 30,
    ) -> None:
        norm_bg, ext_id_tok = normalize_bg_token(bg_token)
        self.refresh_token = refresh_token.strip() if refresh_token else ""
        self.id_token = (id_token.strip() if id_token else "") or (ext_id_tok or "")
        self.push_filename = normalize_push_filename(push_filename)
        self.bg_token = norm_bg
        self.firebase_api_key = firebase_api_key
        self.push_id = push_id
        self.scotty_basic_auth = scotty_basic_auth
        self.config_path = Path(config_path) if config_path else None
        self.timeout = timeout
        self.user_agent = DEFAULT_USER_AGENT
        self._lock = threading.Lock()

        if user_id:
            self.user_id = user_id
        elif self.push_filename.startswith("users/"):
            parts = self.push_filename.split("/")
            self.user_id = parts[1] if len(parts) >= 2 else ""
        else:
            claims = decode_jwt_payload(self.id_token)
            self.user_id = claims.get("user_id") or claims.get("sub") or ""

    @classmethod
    def from_config(cls, config_path: Union[str, Path] = "config.json") -> "SynthIDClient":
        """Load client credentials and session tokens from a JSON configuration file."""
        path = Path(config_path)
        if not path.is_file():
            raise SynthIDError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cls(
            refresh_token=cfg.get("refresh_token", ""),
            push_filename=cfg["push_filename"],
            bg_token=cfg["bg_token"],
            id_token=cfg.get("id_token", ""),
            user_id=cfg.get("user_id", ""),
            firebase_api_key=cfg.get("firebase_api_key", DEFAULT_FIREBASE_API_KEY),
            push_id=cfg.get("push_id", DEFAULT_PUSH_ID),
            scotty_basic_auth=cfg.get("scotty_basic_auth", DEFAULT_SCOTTY_BASIC_AUTH),
            config_path=path,
        )

    def _request_with_retry(
        self,
        req: urllib.request.Request,
        retries: int = 3,
    ) -> Tuple[int, Dict[str, str], bytes]:
        """Execute an HTTP request with exponential backoff on transient network/SSL errors."""
        last_err: Optional[Exception] = None
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    status = resp.status
                    headers = {k.lower(): v for k, v in resp.headers.items()}
                    return status, headers, resp.read()
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="replace")
                raise SynthIDError(f"HTTP {exc.code} on {req.full_url}: {err_body[:500]}") from exc
            except Exception as exc:
                last_err = exc
                if attempt + 1 < retries:
                    time.sleep(0.6 * (attempt + 1))
        raise SynthIDError(f"Network request failed after {retries} attempts: {last_err}") from last_err

    def is_token_expired(self, skew_seconds: int = 180) -> bool:
        """Check whether the current Firebase JWT `id_token` is missing, expired, or close to expiring."""
        return is_jwt_expired(self.id_token, skew_seconds=skew_seconds)

    def ensure_valid_id_token(self, force_refresh: bool = False) -> str:
        """Automatically refresh `id_token` using `refresh_token` if needed."""
        if not force_refresh and not self.is_token_expired():
            return self.id_token
        if not self.refresh_token:
            raise SynthIDError("Firebase id_token is expired/missing and no refresh_token is configured.")

        url = f"{SECURETOKEN_URL}?key={self.firebase_api_key}"
        form_body = urllib.parse.urlencode(
            {"grant_type": "refresh_token", "refresh_token": self.refresh_token}
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=form_body,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://synthid.com",
                "Referer": "https://synthid.com/",
                "User-Agent": self.user_agent,
            },
        )
        _, _, raw = self._request_with_retry(req)
        data = json.loads(raw.decode("utf-8"))
        self.id_token = data["id_token"]
        if data.get("refresh_token"):
            self.refresh_token = data["refresh_token"]
        if data.get("user_id"):
            self.user_id = data["user_id"]

        self._persist_config()
        return self.id_token

    def _persist_config(self) -> None:
        """Persist updated tokens back to `config.json` if configured."""
        if not (self.config_path and self.config_path.is_file()):
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["refresh_token"] = self.refresh_token
            cfg["push_filename"] = self.push_filename
            cfg["bg_token"] = self.bg_token
            cfg["id_token"] = self.id_token
            cfg["user_id"] = self.user_id
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def upload_bytes(self, data: bytes, filename: str = "media.bin") -> str:
        """
        Execute the 2-stage Scotty Resumable Upload protocol (`_.u9` / `_.t9`)
        and return the internal Blobstore path (`blob_id`).
        """
        size = len(data)
        if size == 0:
            raise SynthIDError("Cannot upload an empty file (0 bytes).")
        if size > MAX_FILE_SIZE_BYTES:
            raise SynthIDError(f"File size ({size} bytes) exceeds 100 MB limit ({MAX_FILE_SIZE_BYTES} bytes).")

        common_headers = {
            "Authorization": self.scotty_basic_auth,
            "Push-ID": self.push_id,
            "Push-Filename": self.push_filename,
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": "https://synthid.com",
            "Referer": "https://synthid.com/",
            "User-Agent": self.user_agent,
        }

        start_req = urllib.request.Request(
            SCOTTY_UPLOAD_URL,
            data=f"file_name {Path(filename).name}".encode("utf-8"),
            method="POST",
            headers={
                **common_headers,
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(size),
            },
        )
        _, start_headers, _ = self._request_with_retry(start_req)
        upload_url = start_headers.get("x-goog-upload-url")
        if not upload_url:
            raise SynthIDError(f"Scotty upload initialization did not return X-Goog-Upload-URL: {start_headers}")

        upload_req = urllib.request.Request(
            upload_url,
            data=data,
            method="POST",
            headers={
                **common_headers,
                "X-Goog-Upload-Command": "upload, finalize",
                "X-Goog-Upload-Offset": "0",
            },
        )
        _, upload_headers, upload_body = self._request_with_retry(upload_req)
        upload_status = upload_headers.get("x-goog-upload-status", "")
        blob_id = upload_body.decode("utf-8").strip()
        if upload_status != "final" or not blob_id:
            raise SynthIDError(f"Scotty upload finalize failed (status={upload_status!r}, body={blob_id!r})")
        return blob_id

    def detect_blob(
        self,
        blob_id: str,
        mime_type: str,
        fps: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Call `batchexecute?rpcids=TZRKo` to verify watermark on an uploaded Scotty `blob_id`."""
        self.ensure_valid_id_token()
        modality = get_modality(mime_type)

        media_entry = [mime_type, None, None, None, None, None, blob_id]
        if modality == "video":
            video_fps = int(fps) if fps is not None else 30
            inner_req = [None, [[media_entry]], 0, None, None, None, video_fps]
        else:
            inner_req = [None, [[media_entry]], 0]

        inner_json = json.dumps(inner_req, separators=(",", ":"))
        f_req = json.dumps([[["TZRKo", inner_json, None, "generic"]]], separators=(",", ":"))
        form_data = urllib.parse.urlencode({"f.req": f_req}).encode("utf-8")

        query = urllib.parse.urlencode(
            {
                "rpcids": "TZRKo",
                "source-path": f"/{modality}-detection",
                "hl": "en-US",
                "rt": "c",
            }
        )
        ext_header = json.dumps(
            [self.id_token, None, None, None, None, self.bg_token],
            separators=(",", ":"),
        )

        req = urllib.request.Request(
            f"{BATCHEXECUTE_URL}?{query}",
            data=form_data,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
                "X-Same-Domain": "1",
                "X-Goog-Ext-525006476-Jspb": ext_header,
                "Origin": "https://synthid.com",
                "Referer": "https://synthid.com/",
                "User-Agent": self.user_agent,
            },
        )
        _, _, raw_body = self._request_with_retry(req)
        return parse_batchexecute_response(
            raw_body.decode("utf-8", errors="replace"),
            modality=modality,
            mime_type=mime_type,
            blob_id=blob_id,
        )

    def detect_bytes(
        self,
        data: bytes,
        filename: str,
        mime_type: Optional[str] = None,
        fps: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Thread-safe end-to-end Scotty upload and SynthID watermark verification."""
        resolved_mime = guess_mime_type(filename, mime_type)
        get_modality(resolved_mime)
        with self._lock:
            blob_id = self.upload_bytes(data, filename=filename)
            return self.detect_blob(blob_id=blob_id, mime_type=resolved_mime, fps=fps)

    def detect_file(
        self,
        file_path: Union[str, Path],
        mime_type: Optional[str] = None,
        fps: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Read a local media file and run SynthID watermark detection."""
        path = Path(file_path)
        if not path.is_file():
            raise SynthIDError(f"File not found: {path}")
        return self.detect_bytes(data=path.read_bytes(), filename=path.name, mime_type=mime_type, fps=fps)
