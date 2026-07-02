"""ID、JSON 序列化、哈希和安全文件名相关的小型辅助函数。"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any


def new_id(prefix: str) -> str:
    """为数据库记录和文件生成带前缀的短 ID。"""
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def to_json(data: Any) -> str:
    """把数据序列化成紧凑 JSON，同时保留中文等非 ASCII 文本的可读性。"""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def from_json(data: str | None, default: Any = None) -> Any:
    """反序列化 JSON，并在输入为空时回退到默认值。"""
    if not data:
        return default
    return json.loads(data)


def sha256_file(path: Path) -> str:
    """对磁盘文件做哈希计算，而不一次性读入全部内容。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def sha256_bytes(data: bytes) -> str:
    """对内存中的字节串做哈希计算。"""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def safe_filename(name: str) -> str:
    """去掉文件系统不安全字符，并把交付文件名限制在安全长度内。"""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:180] or "未命名材料"
