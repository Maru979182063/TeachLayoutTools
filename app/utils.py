"""Small utility helpers for IDs, JSON serialization, hashes, and safe filenames."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any


def new_id(prefix: str) -> str:
    """Generate a short prefixed identifier for database rows and files."""
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def to_json(data: Any) -> str:
    """Serialize data to compact JSON while keeping non-ASCII text readable."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def from_json(data: str | None, default: Any = None) -> Any:
    """Deserialize JSON and fall back to a default value for empty input."""
    if not data:
        return default
    return json.loads(data)


def sha256_file(path: Path) -> str:
    """Hash a file on disk without loading the whole file into memory."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def sha256_bytes(data: bytes) -> str:
    """Hash an in-memory byte string."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def safe_filename(name: str) -> str:
    """Remove filesystem-unsafe characters and clamp delivery filenames to a safe length."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:180] or "未命名材料"
