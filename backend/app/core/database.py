"""SQLAlchemy engine and session factory."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def build_engine(url: str | None = None):
    s = get_settings()
    return create_engine(
        url or s.database_url,
        pool_size=s.db_pool_size,
        max_overflow=s.db_max_overflow,
        pool_pre_ping=True,
    )


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def get_session_factory() -> sessionmaker[Session]:
    global _engine, _SessionLocal
    if _SessionLocal is None:
        _engine = build_engine()
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _SessionLocal


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one transaction-scoped session per request."""
    with get_session_factory()() as session:
        yield session
