# -*- coding: utf-8 -*-
"""Boq `batchexecute` frame parser and `TZRKo` Protobuf (JSPB) response decoder."""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

from .constants import ABSTENTION_REASONS, CONFIDENCE_ENUM_MAP, QUOTA_TYPE_MAP
from .exceptions import SynthIDError


def _parse_quota_error(payload: List[Any]) -> Optional[Dict[str, Any]]:
    """Parse Field 7 (index 6) quota exceeded info (`_.U7` -> `_.v7`)."""
    if len(payload) < 7 or not payload[6]:
        return None
    quota_container = payload[6]
    quota_info = quota_container[0] if isinstance(quota_container, list) and quota_container else None
    if not isinstance(quota_info, list):
        return None
    q_type = quota_info[2] if len(quota_info) >= 3 else None
    dur = quota_info[3] if len(quota_info) >= 4 and isinstance(quota_info[3], list) else None
    retry_sec = int(dur[0]) if dur and len(dur) >= 1 and dur[0] is not None else 0
    return {
        "type_code": q_type,
        "media_type": QUOTA_TYPE_MAP.get(q_type, "unknown"),
        "retry_after_seconds": retry_sec,
    }


def _parse_confidence(entry: List[Any]) -> Optional[Dict[str, Any]]:
    """Parse Image Field 4 (false-positive rate) & Field 13 (confidence enum)."""
    raw_fp_rate = entry[3] if len(entry) >= 4 else None
    conf_enum = entry[12] if len(entry) >= 13 else None
    if raw_fp_rate is None and conf_enum is None:
        return None

    level = CONFIDENCE_ENUM_MAP.get(conf_enum)
    score_pct: Optional[float] = None
    if isinstance(raw_fp_rate, (int, float)):
        score_pct = 100.0 - float(raw_fp_rate)
        if not level:
            if score_pct >= 99.999:
                level = "Very High"
            elif score_pct >= 99.9:
                level = "High"
            elif score_pct >= 99.7:
                level = "Mid"
    return {
        "level": level,
        "score_percent": score_pct,
        "raw_fp_rate": raw_fp_rate,
        "enum_code": conf_enum,
    }


def _parse_heatmap(entry: List[Any]) -> Optional[Dict[str, Any]]:
    """Parse Image Field 7 spatial heatmap logits and compute Sigmoid grid probabilities."""
    logits = entry[6] if len(entry) >= 7 and isinstance(entry[6], list) else None
    if not logits:
        return None

    n = len(logits)
    grid_size = int(math.isqrt(n))
    probs_flat = [1.0 / (1.0 + math.exp(-float(x))) for x in logits]
    labels_flat = [
        "detected" if p > 0.8 else ("not_detected" if p < 0.2 else "uncertain")
        for p in probs_flat
    ]
    if grid_size * grid_size == n and grid_size > 0:
        probs_grid = [probs_flat[i * grid_size : (i + 1) * grid_size] for i in range(grid_size)]
        labels_grid = [labels_flat[i * grid_size : (i + 1) * grid_size] for i in range(grid_size)]
    else:
        probs_grid = [probs_flat]
        labels_grid = [labels_flat]

    return {
        "grid_size": grid_size,
        "logits": logits,
        "probabilities": probs_grid,
        "labels": labels_grid,
    }


def _parse_segments(raw_segments: Any) -> List[Dict[str, float]]:
    """Parse `_.V7` time segment arrays `[[start_sec, end_sec], ...]`."""
    if not isinstance(raw_segments, list):
        return []
    return [
        {
            "start_sec": float(seg[0]) if len(seg) >= 1 and seg[0] is not None else 0.0,
            "end_sec": float(seg[1]) if len(seg) >= 2 and seg[1] is not None else 0.0,
        }
        for seg in raw_segments
        if isinstance(seg, list)
    ]


def decode_tzrko_payload(
    payload: List[Any],
    modality: str = "image",
    mime_type: str = "",
    blob_id: str = "",
) -> Dict[str, Any]:
    """Decode the inner `TZRKo` JSPB array into a structured result dictionary."""
    result: Dict[str, Any] = {
        "modality": modality,
        "mime_type": mime_type,
        "blob_id": blob_id,
        "status": "unknown",
        "status_code": None,
        "detected": False,
        "provider_type": None,
        "partner": None,
        "abstention_code": None,
        "abstention_reason": None,
        "abstention_message": None,
        "confidence": None,
        "heatmap": None,
        "audio_segments": [],
        "video_segments": [],
        "has_audio_track": None,
        "quota_error": None,
        "raw_payload": payload,
    }

    quota_error = _parse_quota_error(payload)
    if quota_error:
        result["status"] = "quota_exceeded"
        result["quota_error"] = quota_error
        return result

    results_list = payload[4] if len(payload) >= 5 and isinstance(payload[4], list) else []
    if not results_list or not isinstance(results_list[0], list):
        return result

    entry = results_list[0]
    status_code = entry[0] if len(entry) >= 1 else None
    result["status_code"] = status_code

    if status_code == 3:
        result["status"] = "detected"
        result["detected"] = True
        prov_type = entry[10] if len(entry) >= 11 else None
        partner_name = entry[11] if len(entry) >= 12 else None
        if prov_type == 2:
            result["provider_type"] = "partner"
            result["partner"] = partner_name or "partner"
        else:
            result["provider_type"] = "google"
            result["partner"] = partner_name or "google"
    elif status_code == 2:
        result["status"] = "not_detected"
    elif status_code == 1:
        result["status"] = "uncertain"
        abs_code = entry[9] if len(entry) >= 10 and entry[9] is not None else 0
        reason_key, reason_msg = ABSTENTION_REASONS.get(abs_code, ABSTENTION_REASONS[0])
        if abs_code == 7:
            reason_key = f"{modality}_too_long"
        result["abstention_code"] = abs_code
        result["abstention_reason"] = reason_key
        result["abstention_message"] = reason_msg

    result["confidence"] = _parse_confidence(entry)
    result["heatmap"] = _parse_heatmap(entry)

    if len(entry) >= 2 and isinstance(entry[1], list):
        result["audio_segments"] = _parse_segments(entry[1])

    video_info = entry[7] if len(entry) >= 8 and isinstance(entry[7], list) else None
    if video_info:
        result["audio_segments"] = _parse_segments(video_info[0] if len(video_info) >= 1 else None)
        result["video_segments"] = _parse_segments(video_info[1] if len(video_info) >= 2 else None)
        result["has_audio_track"] = bool(video_info[2]) if len(video_info) >= 3 and video_info[2] is not None else False

    return result


def parse_batchexecute_response(
    raw_text: str,
    modality: str = "image",
    mime_type: str = "",
    blob_id: str = "",
) -> Dict[str, Any]:
    """Extract the `wrb.fr` frame for `TZRKo` from a Boq chunked response and decode it."""
    cleaned = raw_text.lstrip()
    if cleaned.startswith(")]}'"):
        cleaned = cleaned[4:].lstrip()

    wrb_entry = None
    for line in cleaned.splitlines():
        line_str = line.strip()
        if not line_str.startswith("["):
            continue
        try:
            parsed_line = json.loads(line_str)
            if isinstance(parsed_line, list):
                for item in parsed_line:
                    if isinstance(item, list) and len(item) >= 3 and item[0] == "wrb.fr" and item[1] == "TZRKo":
                        wrb_entry = item
                        break
            if wrb_entry:
                break
        except Exception:
            continue

    if not wrb_entry:
        raise SynthIDError(f"Could not find wrb.fr TZRKo frame in response: {raw_text[:500]}")

    payload_str = wrb_entry[2]
    error_ext = wrb_entry[5] if len(wrb_entry) > 5 else None
    if payload_str is None:
        raise SynthIDError(
            f"TZRKo RPC returned error frame: {json.dumps(error_ext)} "
            "(3=INVALID_ARGUMENT, 7=PERMISSION_DENIED/bg_token mismatch, 13=INTERNAL/blob consumed, 16=UNAUTHENTICATED)"
        )

    return decode_tzrko_payload(
        json.loads(payload_str),
        modality=modality,
        mime_type=mime_type,
        blob_id=blob_id,
    )
