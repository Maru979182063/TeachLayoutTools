"""FastAPI 接口入口，负责上传、任务生命周期、运行时信息和产物分发。"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlalchemy.orm import Session

from .config import ARTIFACT_DIR, CONVERTED_DIR, DATA_DIR, TMP_DIR, UPLOAD_DIR, settings
from .database import Base, engine, get_db
from .models import Artifact, FileRecord, JobEvent, JobFile, JobStatus, MaterialJob, NotificationEvent, TERMINAL_STATUSES
from .model_provider import model_config_status, update_runtime_model_config
from .naming import generate_external_name
from .schemas import ApproveJobRequest, BatchCreateJobRequest, CreateJobRequest, JobResponse, UpdateModelConfigRequest
from .storage import detect_type, remove_artifact_file, save_upload
from .targeting import infer_target_from_filename
from .utils import from_json, new_id, to_json
from .workflow import (
    approve_job,
    ensure_worker_pool_started,
    requeue_incomplete_jobs,
    request_cancel,
    runtime_status,
    start_job,
    stop_worker_pool,
)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Async Material Generation MVP-0")
app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static")

USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9._@-]{3,64}$")


@app.on_event("startup")
def startup_runtime() -> None:
    """API 启动时拉起后台工作线程，并恢复最近未完成的任务。"""
    ensure_worker_pool_started()
    requeue_incomplete_jobs()


@app.on_event("shutdown")
def shutdown_runtime() -> None:
    """API 进程关闭时停止工作线程。"""
    stop_worker_pool()


def current_user(
    x_user_id: str | None = Header(default=None),
    x_access_key: str | None = Header(default=None),
) -> str:
    """从请求头解析当前调用者，并按需校验访问密钥。"""
    if settings.require_access_key:
        if not x_access_key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"error_code": "ACCESS_KEY_REQUIRED"})
        if x_access_key != settings.access_key:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"error_code": "ACCESS_KEY_INVALID"})
    user_id = (x_user_id or "").strip()
    if not user_id:
        if settings.allow_demo_user:
            return "demo-user"
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"error_code": "USER_ID_REQUIRED"})
    if not USER_ID_PATTERN.fullmatch(user_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error_code": "USER_ID_INVALID"})
    return user_id


def ensure_queue_capacity(db: Session, user_id: str, requested_jobs: int = 1) -> None:
    """当用户级或全局队列上限将被突破时，拒绝继续入队。"""
    user_pending = (
        db.query(func.count(MaterialJob.id))
        .filter(MaterialJob.owner_id == user_id, MaterialJob.status.not_in(tuple(TERMINAL_STATUSES)))
        .scalar()
        or 0
    )
    if user_pending + requested_jobs > settings.queue_max_pending_per_user:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error_code": "USER_PENDING_LIMIT_EXCEEDED"},
        )

    total_pending = (
        db.query(func.count(MaterialJob.id))
        .filter(MaterialJob.status.not_in(tuple(TERMINAL_STATUSES)))
        .scalar()
        or 0
    )
    if total_pending + requested_jobs > settings.queue_max_pending_total:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error_code": "GLOBAL_PENDING_LIMIT_EXCEEDED"},
        )


def job_to_response(job: MaterialJob) -> JobResponse:
    """把 MaterialJob 数据行转换成对外返回的响应结构。"""
    return JobResponse(
        job_id=job.id,
        status=job.status.value,
        current_step=job.current_step,
        render_attempt=job.render_attempt,
        review_round=job.review_round,
        auto_fix_count=job.auto_fix_count,
        max_auto_fix_count=job.max_auto_fix_count,
        cancel_requested=job.cancel_requested,
        user_visible_message=job.user_visible_message,
        next_action=job.next_action,
    )


@app.get("/health")
def health():
    """返回最小健康检查结果，供探针和本地冒烟测试使用。"""
    return {"status": "ok"}


@app.get("/api/runtime")
def runtime_info(user_id: str = Depends(current_user)):
    """返回队列、鉴权和存储设置，方便解释当前运行时状态。"""
    ensure_worker_pool_started()
    return {
        "user_id": user_id,
        "queue": runtime_status(),
        "limits": {
            "max_pending_jobs_total": settings.queue_max_pending_total,
            "max_pending_jobs_per_user": settings.queue_max_pending_per_user,
            "max_batch_files": settings.batch_upload_limit,
        },
        "auth": {
            "require_access_key": settings.require_access_key,
            "allow_demo_user": settings.allow_demo_user,
        },
        "storage": {
            "data_dir": str(DATA_DIR),
            "upload_dir": str(UPLOAD_DIR),
            "artifact_dir": str(ARTIFACT_DIR),
            "converted_dir": str(CONVERTED_DIR),
            "tmp_dir": str(TMP_DIR),
        },
    }


@app.get("/api/model-config")
def model_config():
    """返回当前实际生效的模型规划配置。"""
    return model_config_status()


@app.put("/api/model-config")
def put_model_config(request: UpdateModelConfigRequest):
    """保存工作台或 API 客户端提交的运行时模型配置覆盖项。"""
    return update_runtime_model_config(
        api_key=request.api_key,
        model=request.model,
        base_url=request.base_url,
        enabled=request.enabled,
    )


@app.get("/", response_class=HTMLResponse)
def workbench():
    """返回本地轻量工作台页面。"""
    return FileResponse(Path(__file__).resolve().parent / "static" / "workbench.html")


@app.post("/api/files")
async def upload_file(
    file: UploadFile = File(...),
    file_role: str = "source_material",
    db: Session = Depends(get_db),
    user_id: str = Depends(current_user),
):
    """保存上传的源文件，并把元数据登记到数据库。"""
    try:
        file_type, mime_type = detect_type(file.filename or "", file.content_type or "")
        path, size, checksum = await save_upload(file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error_code": str(exc)})
    naming = generate_external_name(file.filename or "未命名材料", {}, "", Path(file.filename or "").suffix.lower())
    record = FileRecord(
        id=path.stem,
        owner_id=user_id,
        org_id=None,
        file_role=file_role,
        original_name=file.filename or path.name,
        normalized_name=naming["display_name"],
        file_type=file_type,
        mime_type=mime_type,
        storage_path=str(path),
        checksum=checksum,
        size_bytes=size,
        status="uploaded",
        naming_metadata_json=to_json(naming),
    )
    db.add(record)
    db.commit()
    return {
        "file_id": record.id,
        "file_name": record.original_name,
        "normalized_name": record.normalized_name,
        "file_type": record.file_type,
        "mime_type": record.mime_type,
        "size_bytes": record.size_bytes,
        "checksum": record.checksum,
        "status": record.status,
        "naming": naming,
    }


@app.post("/api/material-jobs", response_model=JobResponse)
def create_job(request: CreateJobRequest, db: Session = Depends(get_db), user_id: str = Depends(current_user)):
    """根据明确给出的源文件 ID 创建单个材料生成任务。"""
    if not request.source_file_ids:
        raise HTTPException(status_code=400, detail={"error_code": "FILE_NOT_FOUND"})
    ensure_queue_capacity(db, user_id, len(request.source_file_ids))
    files = db.query(FileRecord).filter(FileRecord.id.in_(request.source_file_ids)).all()
    if len(files) != len(request.source_file_ids):
        raise HTTPException(status_code=404, detail={"error_code": "FILE_NOT_FOUND"})
    if any(f.owner_id != user_id for f in files):
        raise HTTPException(status_code=403, detail={"error_code": "FILE_PERMISSION_DENIED"})
    job = MaterialJob(
        id=new_id("JOB"),
        owner_id=user_id,
        org_id=None,
        status=JobStatus.QUEUED,
        target_json=to_json(request.target),
        options_json=to_json(request.options),
        max_auto_fix_count=int(request.options.get("max_auto_fix_count", 3)),
        current_step="queued",
        user_visible_message="任务已创建，正在后台生成",
    )
    db.add(job)
    db.flush()
    for file_record in files:
        db.add(JobFile(id=new_id("JF"), job_id=job.id, file_id=file_record.id, role=file_record.file_role))
    db.commit()
    start_job(job.id)
    db.refresh(job)
    return job_to_response(job)


@app.post("/api/batch/material-jobs")
def create_batch_jobs(request: BatchCreateJobRequest, db: Session = Depends(get_db), user_id: str = Depends(current_user)):
    """按文件名自动推断目标信息，并为每个上传文件创建任务。"""
    if not request.source_file_ids:
        raise HTTPException(status_code=400, detail={"error_code": "FILE_NOT_FOUND"})
    if len(request.source_file_ids) > settings.batch_upload_limit:
        raise HTTPException(status_code=400, detail={"error_code": "BATCH_LIMIT_EXCEEDED"})
    ensure_queue_capacity(db, user_id, len(request.source_file_ids))
    files = db.query(FileRecord).filter(FileRecord.id.in_(request.source_file_ids)).all()
    by_id = {file.id: file for file in files}
    results = []
    for file_id in request.source_file_ids:
        file_record = by_id.get(file_id)
        if not file_record:
            results.append({"file_id": file_id, "status": "rejected", "error_code": "FILE_NOT_FOUND"})
            continue
        if file_record.owner_id != user_id:
            results.append({"file_id": file_id, "status": "rejected", "error_code": "FILE_PERMISSION_DENIED"})
            continue
        target = infer_target_from_filename(file_record.original_name)
        job = MaterialJob(
            id=new_id("JOB"),
            owner_id=user_id,
            org_id=None,
            status=JobStatus.QUEUED,
            target_json=to_json(target),
            options_json=to_json(request.options),
            max_auto_fix_count=int(request.options.get("max_auto_fix_count", 3)),
            current_step="queued",
            user_visible_message="任务已创建，正在后台生成",
        )
        db.add(job)
        db.flush()
        db.add(JobFile(id=new_id("JF"), job_id=job.id, file_id=file_record.id, role=file_record.file_role))
        db.commit()
        start_job(job.id)
        results.append({"file_id": file_id, "job_id": job.id, "status": "queued", "target": target})
    return {"jobs": results}


@app.get("/api/material-jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, db: Session = Depends(get_db), user_id: str = Depends(current_user)):
    """如果任务属于当前用户，则返回该任务。"""
    job = db.get(MaterialJob, job_id)
    if not job or job.owner_id != user_id:
        raise HTTPException(status_code=404, detail={"error_code": "JOB_NOT_FOUND"})
    return job_to_response(job)


@app.get("/api/material-jobs")
def list_jobs(db: Session = Depends(get_db), user_id: str = Depends(current_user)):
    """列出当前用户最近创建的任务。"""
    jobs = (
        db.query(MaterialJob)
        .filter(MaterialJob.owner_id == user_id)
        .order_by(MaterialJob.created_at.desc())
        .limit(50)
        .all()
    )
    return {"jobs": [job_to_response(job).model_dump() for job in jobs]}


@app.post("/api/material-jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: str, db: Session = Depends(get_db), user_id: str = Depends(current_user)):
    """取消当前用户拥有的排队中或运行中任务。"""
    job = db.get(MaterialJob, job_id)
    if not job or job.owner_id != user_id:
        raise HTTPException(status_code=404, detail={"error_code": "JOB_NOT_FOUND"})
    try:
        request_cancel(db, job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error_code": str(exc)})
    db.refresh(job)
    return job_to_response(job)


@app.get("/api/material-jobs/{job_id}/artifacts")
def list_artifacts(job_id: str, db: Session = Depends(get_db), user_id: str = Depends(current_user)):
    """列出单个任务的全部产物，包括审核状态和下载状态。"""
    job = db.get(MaterialJob, job_id)
    if not job or job.owner_id != user_id:
        raise HTTPException(status_code=404, detail={"error_code": "JOB_NOT_FOUND"})
    return {
        "job_id": job.id,
        "artifacts": [
            {
                "artifact_id": a.id,
                "type": a.artifact_type,
                "name": a.name,
                "display_name": a.display_name,
                "download_name": a.download_name,
                "name_confidence": a.name_confidence / 100,
                "attempt": a.attempt,
                "version": a.version,
                "is_current": a.is_current,
                "approved_for_download": a.approved_for_download,
                "download_url": f"/api/artifacts/{a.id}/download" if a.approved_for_download else None,
                "metadata": from_json(a.metadata_json, {}),
            }
            for a in sorted(job.artifacts, key=lambda item: item.created_at)
        ],
    }


@app.post("/api/material-jobs/{job_id}/review/approve", response_model=JobResponse)
def approve(job_id: str, request: ApproveJobRequest, db: Session = Depends(get_db), user_id: str = Depends(current_user)):
    """批准处于人工审核中的任务，并解锁可下载产物。"""
    job = db.get(MaterialJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error_code": "JOB_NOT_FOUND"})
    try:
        approve_job(db, job, user_id, request.final_download_name)
    except ValueError as exc:
        code = str(exc)
        status_code = 403 if "PERMISSION" in code else 409
        raise HTTPException(status_code=status_code, detail={"error_code": code})
    return job_to_response(job)


@app.get("/api/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str, db: Session = Depends(get_db), user_id: str = Depends(current_user)):
    """在文件仍存在时下载已经批准的产物。"""
    artifact = db.get(Artifact, artifact_id)
    if not artifact or artifact.owner_id != user_id:
        raise HTTPException(status_code=404, detail={"error_code": "ARTIFACT_NOT_FOUND"})
    if not artifact.approved_for_download:
        raise HTTPException(status_code=409, detail={"error_code": "ARTIFACT_NOT_APPROVED"})
    path = Path(artifact.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail={"error_code": "ARTIFACT_FILE_MISSING"})
    return FileResponse(path, media_type=artifact.content_type, filename=artifact.download_name)


@app.get("/api/artifacts/{artifact_id}/preview")
def preview_artifact(artifact_id: str, db: Session = Depends(get_db), user_id: str = Depends(current_user)):
    """直接从存储中预览可审核产物，而不把它标记成可下载。"""
    artifact = db.get(Artifact, artifact_id)
    if not artifact or artifact.owner_id != user_id:
        raise HTTPException(status_code=404, detail={"error_code": "ARTIFACT_NOT_FOUND"})
    if artifact.artifact_type not in {"html", "pdf", "rule_review_report", "visual_review_report", "input_quality", "parsed_content", "material_spec", "material_plan"}:
        raise HTTPException(status_code=409, detail={"error_code": "ARTIFACT_NOT_PREVIEWABLE"})
    path = Path(artifact.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail={"error_code": "ARTIFACT_FILE_MISSING"})
    return FileResponse(path, media_type=artifact.content_type)


@app.get("/api/material-jobs/{job_id}/logs")
def get_logs(job_id: str, db: Session = Depends(get_db), user_id: str = Depends(current_user)):
    """返回单个任务对用户可见的工作流事件日志。"""
    job = db.get(MaterialJob, job_id)
    if not job or job.owner_id != user_id:
        raise HTTPException(status_code=404, detail={"error_code": "JOB_NOT_FOUND"})
    events = db.query(JobEvent).filter(JobEvent.job_id == job_id, JobEvent.user_visible.is_(True)).order_by(JobEvent.created_at).all()
    return {
        "job_id": job_id,
        "events": [
            {
                "event_type": e.event_type,
                "from_status": e.from_status,
                "to_status": e.to_status,
                "level": e.level,
                "message": e.message,
                "created_at": e.created_at,
            }
            for e in events
        ],
    }


@app.get("/api/material-jobs/{job_id}/notifications")
def get_notifications(job_id: str, db: Session = Depends(get_db), user_id: str = Depends(current_user)):
    """返回单个任务产生过的通知记录。"""
    job = db.get(MaterialJob, job_id)
    if not job or job.owner_id != user_id:
        raise HTTPException(status_code=404, detail={"error_code": "JOB_NOT_FOUND"})
    rows = db.query(NotificationEvent).filter(NotificationEvent.job_id == job_id).order_by(NotificationEvent.created_at).all()
    return {
        "job_id": job_id,
        "notifications": [
            {
                "event_type": n.event_type,
                "target_status": n.target_status,
                "channel_type": n.channel_type,
                "status": n.status,
                "payload": from_json(n.payload_json, {}),
                "created_at": n.created_at,
            }
            for n in rows
        ],
    }


@app.post("/api/admin/cleanup-expired-artifacts")
def cleanup_expired_artifacts(
    db: Session = Depends(get_db),
    user_id: str = Depends(current_user),
):
    """删除已过期的产物文件，并清理对应数据库记录。"""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    expired = db.query(Artifact).filter(Artifact.retention_until.is_not(None), Artifact.retention_until < now).all()
    removed = 0
    for artifact in expired:
        remove_artifact_file(artifact)
        db.delete(artifact)
        removed += 1
    db.commit()
    return {"removed": removed}
