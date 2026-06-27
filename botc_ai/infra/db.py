from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from botc_ai.settings import get_settings


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""


def make_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, future=True)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    from botc_ai.infra import orm  # noqa: F401

    Base.metadata.create_all(bind=engine)
