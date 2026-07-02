"""SQLAlchemy engine, session factory, and FastAPI database dependency helpers."""

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
    """Base declarative class shared by all SQLAlchemy models."""
    pass


def get_db():
    """Yield a request-scoped database session and always close it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
