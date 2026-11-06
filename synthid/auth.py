# -*- coding: utf-8 -*-
"""Authentication and token normalization utilities for SynthID Detector."""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from .constants import DROPZONE_PREFIX


def decode_jwt_payload(jwt_token: str) -> Dict[str, Any]:
    """Decode the payload section of a JWT without external dependencies."""
    if not jwt_token:
        return {}
    try:
        parts = jwt_token.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1]
        padding = "=" * ((4 - len(payload_b64) % 4) % 4)
        raw = base64.urlsafe_b64decode(payload_b64 + padding)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def is_jwt_expired(jwt_token: str, skew_seconds: int = 180) -> bool:
    """Return True if the JWT is empty, malformed, or expiring within `skew_seconds`."""
    if not jwt_token:
        return True
    exp = decode_jwt_payload(jwt_token).get("exp")
    if not exp:
        return True
    return time.time() + skew_seconds >= float(exp)


def normalize_push_filename(value: str) -> str:
    """
    Normalize either `users/{uid}/files/{uuid}` or
    `/synthid-demo-drop-zone/ttl_1d/users/{uid}/files/{uuid}` to `users/{uid}/files/{uuid}`.
    """
    cleaned = value.strip()
    if cleaned.startswith(DROPZONE_PREFIX):
        cleaned = cleaned[len(DROPZONE_PREFIX) :]
    return cleaned.lstrip("/")


def normalize_bg_token(value: Union[str, List[Any]]) -> Tuple[str, Optional[str]]:
    """
    Accept either a raw `!sLO...` BotGuard snapshot string or the full
    `x-goog-ext-525006476-jspb` JSON array (`["<id_token>",null,null,null,null,"!<bg_token>"]`).
    Returns `(bg_token, extracted_id_token_or_None)`.
    """
    if isinstance(value, list):
        id_tok = value[0] if len(value) >= 1 and isinstance(value[0], str) else None
        bg_tok = value[5] if len(value) >= 6 and isinstance(value[5], str) else ""
        return bg_tok, id_tok

    cleaned = value.strip()
    if cleaned.startswith("["):
        try:
            arr = json.loads(cleaned)
            if isinstance(arr, list):
                id_tok = arr[0] if len(arr) >= 1 and isinstance(arr[0], str) else None
                bg_tok = arr[5] if len(arr) >= 6 and isinstance(arr[5], str) else ""
                return bg_tok, id_tok
        except Exception:
            pass
    return cleaned, None
