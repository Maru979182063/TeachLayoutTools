"""Pydantic request and response schemas exposed by the API layer."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateJobRequest(BaseModel):
    """Request body for creating a job from uploaded source files."""
    source_file_ids: list[str]
    target: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    notify: dict[str, Any] = Field(default_factory=dict)


class ApproveJobRequest(BaseModel):
    """Request body for human approval of a reviewed job."""
    final_download_name: str | None = None


class BatchCreateJobRequest(BaseModel):
    """Request body for creating many jobs from a batch of files."""
    source_file_ids: list[str]
    options: dict[str, Any] = Field(default_factory=dict)
    notify: dict[str, Any] = Field(default_factory=dict)


class JobResponse(BaseModel):
    """Public response model returned by job lifecycle endpoints."""
    job_id: str
    status: str
    current_step: str | None = None
    render_attempt: int = 0
    review_round: int = 0
    auto_fix_count: int = 0
    max_auto_fix_count: int = 3
    cancel_requested: bool = False
    user_visible_message: str | None = None
    next_action: str | None = None


class UpdateModelConfigRequest(BaseModel):
    """Request body for updating runtime model configuration overrides."""
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    enabled: bool | None = None
