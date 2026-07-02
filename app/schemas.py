"""API 层对外暴露的 Pydantic 请求和响应结构。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateJobRequest(BaseModel):
    """根据上传源文件创建任务时使用的请求体。"""
    source_file_ids: list[str]
    target: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    notify: dict[str, Any] = Field(default_factory=dict)


class ApproveJobRequest(BaseModel):
    """人工批准审核中任务时使用的请求体。"""
    final_download_name: str | None = None


class BatchCreateJobRequest(BaseModel):
    """根据一批文件创建多个任务时使用的请求体。"""
    source_file_ids: list[str]
    options: dict[str, Any] = Field(default_factory=dict)
    notify: dict[str, Any] = Field(default_factory=dict)


class JobResponse(BaseModel):
    """任务生命周期接口返回的公共响应模型。"""
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
    """更新运行时模型配置覆盖项时使用的请求体。"""
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    enabled: bool | None = None
