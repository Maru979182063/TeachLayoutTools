"""负责把文件落盘、把元数据写进数据库的上传与产物存储辅助函数。"""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from .database import ARTIFACT_DIR, UPLOAD_DIR
from .models import Artifact, MaterialJob
from .naming import generate_external_name
from .utils import new_id, sha256_bytes, sha256_file, to_json


def detect_type(filename: str, content_type: str) -> tuple[str, str]:
    """校验上传文件扩展名，并规范化要保存的 MIME 类型。"""
    ext = Path(filename).suffix.lower()
    allowed = {
        ".pdf": ("pdf", "application/pdf"),
        ".docx": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ".zip": ("zip", "application/zip"),
    }
    if ext not in allowed:
        raise ValueError("FILE_UNSUPPORTED_TYPE")
    file_type, expected_mime = allowed[ext]
    if content_type and content_type not in {expected_mime, "application/octet-stream", "application/x-zip-compressed"}:
        if not (ext == ".zip" and content_type in {"application/x-zip-compressed", "application/octet-stream"}):
            raise ValueError("FILE_MIME_MISMATCH")
    return file_type, expected_mime


async def save_upload(upload: UploadFile) -> tuple[Path, int, str]:
    """把上传文件写入磁盘，并返回路径、大小和校验和。"""
    file_id = new_id("FILE")
    target = UPLOAD_DIR / f"{file_id}{Path(upload.filename or '').suffix.lower()}"
    size = 0
    with target.open("wb") as out:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            out.write(chunk)
    return target, size, sha256_file(target)


def write_artifact(
    db: Session,
    job: MaterialJob,
    artifact_type: str,
    step: str,
    name: str,
    content: bytes,
    content_type: str,
    attempt: int,
    target: dict,
    extracted_text: str = "",
    naming_name: str | None = None,
    reviewed: bool = False,
    success_retention: bool = False,
) -> Artifact:
    """保存一个生成产物文件，并创建对应数据库记录。"""
    artifact_id = new_id("ART")
    ext = Path(name).suffix or ".json"
    path = ARTIFACT_DIR / f"{artifact_id}{ext}"
    path.write_bytes(content)
    naming = generate_external_name(naming_name or name, target, extracted_text, artifact_ext=ext)
    retention_days = 30 if success_retention else 7
    artifact = Artifact(
        id=artifact_id,
        owner_id=job.owner_id,
        org_id=job.org_id,
        job_id=job.id,
        artifact_type=artifact_type,
        step=step,
        attempt=attempt,
        version=attempt,
        name=name,
        display_name=naming["display_name"],
        download_name=naming["download_name"],
        name_confidence=int(naming["confidence"] * 100),
        storage_path=str(path),
        content_type=content_type,
        checksum=sha256_bytes(content),
        size_bytes=len(content),
        approved_for_download=reviewed,
        retention_until=datetime.now(timezone.utc) + timedelta(days=retention_days),
        metadata_json=to_json({"naming": naming}),
    )
    db.add(artifact)
    db.flush()
    return artifact


def remove_artifact_file(artifact: Artifact) -> None:
    """在保留期清理时从磁盘删除产物文件。"""
    path = Path(artifact.storage_path)
    if path.exists():
        path.unlink()


def copy_to_path(src: Path, dst: Path) -> None:
    """把生成文件复制到目标路径，必要时先创建父目录。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
