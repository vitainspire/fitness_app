"""Database session management.

Pooling is Supabase's job in production (STRATEGY §5) — do not self-host a second
pooler. The engine here keeps its own pool small precisely because connection count
scales with process count, not user count.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Declarative base for every model in the application."""


_engine = None
_SessionFactory: sessionmaker[Session] | None = None


def init_engine(database_url: str, echo: bool = False) -> None:
    global _engine, _SessionFactory
    _engine = create_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        # Small pool: Supabase's pooler multiplexes for us. A large pool per
        # process is how max_connections gets exhausted at low traffic.
        pool_size=5,
        max_overflow=5,
    )
    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)


def get_engine():
    if _engine is None:
        raise RuntimeError("init_engine() must be called before get_engine()")
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope. Commits on success, rolls back on any exception."""
    if _SessionFactory is None:
        raise RuntimeError("init_engine() must be called before session_scope()")
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
