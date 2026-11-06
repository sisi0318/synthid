# -*- coding: utf-8 -*-
"""Protocol constants, endpoints, and enum mappings for SynthID Detector."""

from __future__ import annotations

from typing import Dict, Tuple

DEFAULT_FIREBASE_API_KEY = "AIzaSyCjADz3UffE3DF5HZ--7MgVEXrbt6Bgcck"
DEFAULT_PUSH_ID = "feeds/szvztmw263lqqp"
DEFAULT_SCOTTY_BASIC_AUTH = "Basic amI5dChNbS0qNjFlczJJVzpyZyRHNUp1QENpV29OJVNS"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
)

SCOTTY_UPLOAD_URL = "https://content-push.googleapis.com/upload/"
BATCHEXECUTE_URL = "https://synthid.com/_/WatermarkApp/data/batchexecute"
SECURETOKEN_URL = "https://securetoken.googleapis.com/v1/token"

DROPZONE_PREFIX = "/synthid-demo-drop-zone/ttl_1d/"
MAX_FILE_SIZE_BYTES = 104_857_600  # 100 MB

EXTENSION_MIME_MAP: Dict[str, str] = {
    # Image
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".gif": "image/gif",
    # Audio
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".m4a": "audio/mp4",
    # Video
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}

ABSTENTION_REASONS: Dict[int, Tuple[str, str]] = {
    0: ("abstention", "Not enough information to detect SynthID."),
    1: ("empty", "The uploaded media was empty."),
    2: ("low_resolution", "The media resolution is too low."),
    3: ("not_enough_detail", "Not enough details to watermark."),
    4: ("low_fps", "The video has less than 10 frames per second."),
    5: ("low_quality", "The media is too low quality."),
    6: (
        "possible_tampering",
        "We suspect the image has been tampered with or Google AI was only used for a very small part.",
    ),
    7: ("media_too_long", "The media is too long to be processed."),
    8: ("media_too_short", "The media is too short to be processed."),
}

CONFIDENCE_ENUM_MAP: Dict[int, str] = {
    6: "Very High",
    5: "High",
    4: "Mid",
}

QUOTA_TYPE_MAP: Dict[int, str] = {
    1: "image",
    2: "image_similar",
    3: "audio",
    4: "audio_similar",
    5: "video",
    6: "video_similar",
}
