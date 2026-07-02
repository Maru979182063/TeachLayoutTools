"""从环境变量加载运行时配置和文件目录。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def _path_env(name: str, default: Path) -> Path:
    """从环境变量解析目录路径，并确保目录已经创建。"""
    raw = os.getenv(name, "").strip()
    path = Path(raw) if raw else default
    path.mkdir(parents=True, exist_ok=True)
    return path


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    """读取整数型环境变量，并按最小值要求做下限保护。"""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _bool_env(name: str, default: bool = False) -> bool:
    """按常见真值字符串解析布尔型环境变量。"""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


DATA_DIR = _path_env("APP_DATA_DIR", BASE_DIR / "data")
ARTIFACT_DIR = _path_env("APP_ARTIFACT_DIR", DATA_DIR / "artifacts")
UPLOAD_DIR = _path_env("APP_UPLOAD_DIR", DATA_DIR / "uploads")
CONVERTED_DIR = _path_env("APP_CONVERTED_DIR", DATA_DIR / "converted")
TMP_DIR = _path_env("APP_TMP_DIR", DATA_DIR / "tmp")


DEFAULT_SQLITE_URL = f"sqlite:///{(DATA_DIR / 'app.db').as_posix()}"


@dataclass(frozen=True)
class RuntimeSettings:
    """服务全局共享的不可变运行时配置快照。"""
    database_url: str = os.getenv("APP_DATABASE_URL", DEFAULT_SQLITE_URL).strip()
    worker_count: int = _int_env("APP_WORKER_COUNT", 2)
    queue_poll_seconds: int = _int_env("APP_QUEUE_POLL_SECONDS", 1)
    queue_max_pending_total: int = _int_env("APP_MAX_PENDING_JOBS_TOTAL", 200)
    queue_max_pending_per_user: int = _int_env("APP_MAX_PENDING_JOBS_PER_USER", 30)
    batch_upload_limit: int = _int_env("APP_MAX_BATCH_FILES", 20)
    recover_incomplete_jobs: bool = _bool_env("APP_RECOVER_INCOMPLETE_JOBS", True)
    recover_max_age_hours: int = _int_env("APP_RECOVER_MAX_AGE_HOURS", 12)
    require_access_key: bool = _bool_env("APP_REQUIRE_ACCESS_KEY", False)
    access_key: str = os.getenv("APP_ACCESS_KEY", "").strip()
    allow_demo_user: bool = _bool_env("APP_ALLOW_DEMO_USER", False)
    word_max_concurrency: int = _int_env("APP_WORD_MAX_CONCURRENCY", 1)
    word_retry_count: int = _int_env("APP_WORD_RETRY_COUNT", 2)
    force_kill_word_on_timeout: bool = _bool_env("APP_FORCE_KILL_WORD_ON_TIMEOUT", True)


settings = RuntimeSettings()
