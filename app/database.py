"""SQLAlchemy 引擎、会话工厂和 FastAPI 数据库依赖辅助函数。"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import ARTIFACT_DIR, DATA_DIR, UPLOAD_DIR, settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    future=True,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    """所有 SQLAlchemy 模型共用的声明式基类。"""
    pass


def get_db():
    """按请求生成数据库会话，并在结束后始终关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
