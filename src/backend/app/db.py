from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def init_db(url: str) -> Engine:
    """Create the engine and tables. Called once per app instance."""
    global _engine, _SessionLocal
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    _engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(_engine, "connect")
        def _fk_on(dbapi_conn, _):  # SQLite needs this for ON DELETE CASCADE
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(_engine)
    return _engine


def session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        raise RuntimeError("init_db() has not been called")
    return _SessionLocal


def get_db() -> Iterator[Session]:
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()
