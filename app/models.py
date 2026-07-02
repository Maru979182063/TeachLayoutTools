"""Database models and shared workflow enums for files, jobs, artifacts, and notifications."""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    """Return the current UTC timestamp used by model defaults."""
    return datetime.now(timezone.utc)


class JobStatus(str, enum.Enum):
    """Workflow states a material job can move through."""
    CREATED = "CREATED"
    UPLOADED = "UPLOADED"
    QUEUED = "QUEUED"
    PARSING = "PARSING"
    PARSE_FAILED = "PARSE_FAILED"
    PLANNING = "PLANNING"
    PLAN_FAILED = "PLAN_FAILED"
    NORMALIZING = "NORMALIZING"
    NORMALIZE_FAILED = "NORMALIZE_FAILED"
    RENDERING = "RENDERING"
    CONVERT_FAILED = "CONVERT_FAILED"
    RULE_REVIEWING = "RULE_REVIEWING"
    PATCHING = "PATCHING"
    PUBLISHING = "PUBLISHING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    CANCELLED = "CANCELLED"


TERMINAL_STATUSES = {
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.HUMAN_REVIEW_REQUIRED,
    JobStatus.CANCELLED,
}


class FileRecord(Base):
    """Uploaded source file metadata stored before jobs are created."""
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(120), index=True)
    org_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    file_role: Mapped[str] = mapped_column(String(60))
    original_name: Mapped[str] = mapped_column(String(500))
    normalized_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_type: Mapped[str] = mapped_column(String(30))
    mime_type: Mapped[str] = mapped_column(String(200))
    storage_path: Mapped[str] = mapped_column(String(1000))
    checksum: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="uploaded")
    naming_metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MaterialJob(Base):
    """Top-level workflow record for one material-generation request."""
    __tablename__ = "material_jobs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(120), index=True)
    org_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.QUEUED, index=True)
    target_json: Mapped[str] = mapped_column(Text)
    options_json: Mapped[str] = mapped_column(Text)
    standard_profile_id: Mapped[str] = mapped_column(String(120), default="SP_REVIEW_HANDOUT_STUDENT_MATH_DEFAULT")
    render_attempt: Mapped[int] = mapped_column(Integer, default=0)
    review_round: Mapped[int] = mapped_column(Integer, default=0)
    auto_fix_count: Mapped[int] = mapped_column(Integer, default=0)
    max_auto_fix_count: Mapped[int] = mapped_column(Integer, default=3)
    current_step: Mapped[str | None] = mapped_column(String(120), nullable=True)
    current_artifact_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    latest_successful_artifact_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_visible_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    files: Mapped[list["JobFile"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobFile(Base):
    """Join table linking uploaded files to a job with a role label."""
    __tablename__ = "job_files"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("material_jobs.id"))
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id"))
    role: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[MaterialJob] = relationship(back_populates="files")
    file: Mapped[FileRecord] = relationship()


class Artifact(Base):
    """Generated or intermediate file emitted during workflow execution."""
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(120), index=True)
    org_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("material_jobs.id"), index=True)
    artifact_type: Mapped[str] = mapped_column(String(80))
    step: Mapped[str] = mapped_column(String(120))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(500))
    display_name: Mapped[str] = mapped_column(String(500))
    download_name: Mapped[str] = mapped_column(String(500))
    name_confidence: Mapped[float] = mapped_column(Integer, default=0)
    storage_path: Mapped[str] = mapped_column(String(1000))
    content_type: Mapped[str] = mapped_column(String(200))
    checksum: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    approved_for_download: Mapped[bool] = mapped_column(Boolean, default=False)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[MaterialJob] = relationship(back_populates="artifacts")


class JobEvent(Base):
    """User-visible event log entry for workflow progress and failures."""
    __tablename__ = "job_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(40), index=True)
    event_type: Mapped[str] = mapped_column(String(100))
    from_status: Mapped[str | None] = mapped_column(String(60), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(60), nullable=True)
    step: Mapped[str | None] = mapped_column(String(120), nullable=True)
    attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    level: Mapped[str] = mapped_column(String(20), default="info")
    message: Mapped[str] = mapped_column(Text)
    user_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NotificationEvent(Base):
    """Notification delivery record used to deduplicate status pushes."""
    __tablename__ = "notification_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(40), index=True)
    event_type: Mapped[str] = mapped_column(String(100))
    target_status: Mapped[str] = mapped_column(String(60))
    channel_type: Mapped[str] = mapped_column(String(60), default="mock")
    channel_id: Mapped[str] = mapped_column(String(120), default="mock-default")
    dedupe_key: Mapped[str] = mapped_column(String(260), unique=True)
    payload_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
