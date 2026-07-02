"""FastAPI entrypoints for uploads, job lifecycle actions, runtime inspection, and artifact delivery."""

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
    """Start background workers and recover recent unfinished jobs when the API boots."""
    ensure_worker_pool_started()
    requeue_incomplete_jobs()


@app.on_event("shutdown")
def shutdown_runtime() -> None:
    """Stop worker threads when the API process shuts down."""
    stop_worker_pool()


def current_user(
    x_user_id: str | None = Header(default=None),
    x_access_key: str | None = Header(default=None),
) -> str:
    """Resolve the current API caller from request headers and enforce optional access-key checks."""
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
    """Reject new work when per-user or global queue limits would be exceeded."""
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
    """Convert a MaterialJob row into the public API response shape."""
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
    """Return a minimal health check for probes and local smoke tests."""
    return {"status": "ok"}


@app.get("/api/runtime")
def runtime_info(user_id: str = Depends(current_user)):
    """Expose queue, auth, and storage settings that help explain current runtime behavior."""
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
    """Return the currently effective model-planner configuration."""
    return model_config_status()


@app.put("/api/model-config")
def put_model_config(request: UpdateModelConfigRequest):
    """Persist runtime model-planner overrides from the workbench or API client."""
    return update_runtime_model_config(
        api_key=request.api_key,
        model=request.model,
        base_url=request.base_url,
        enabled=request.enabled,
    )


@app.get("/", response_class=HTMLResponse)
def workbench():
    """Serve the lightweight local workbench UI."""
    return FileResponse(Path(__file__).resolve().parent / "static" / "workbench.html")


@app.post("/api/files")
async def upload_file(
    file: UploadFile = File(...),
    file_role: str = "source_material",
    db: Session = Depends(get_db),
    user_id: str = Depends(current_user),
):
    """Store an uploaded source file and register its metadata in the database."""
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
    """Create one material-generation job from explicit source file IDs."""
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
    """Create one job per uploaded file using filename-inferred targets."""
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
    """Return one job if it belongs to the current user."""
    job = db.get(MaterialJob, job_id)
    if not job or job.owner_id != user_id:
        raise HTTPException(status_code=404, detail={"error_code": "JOB_NOT_FOUND"})
    return job_to_response(job)


@app.get("/api/material-jobs")
def list_jobs(db: Session = Depends(get_db), user_id: str = Depends(current_user)):
    """List the most recent jobs created by the current user."""
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
    """Cancel a queued or running job owned by the current user."""
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
    """List all artifacts generated for one job, including review and download state."""
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
    """Approve a job that is waiting in human review and unlock download artifacts."""
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
    """Download an approved artifact file when it still exists on disk."""
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
    """Preview reviewable artifacts directly from storage without marking them downloadable."""
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
    """Return the user-visible workflow event log for one job."""
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
    """Return notification records emitted for one job."""
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
    """Delete expired artifact files and remove their database rows."""
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
