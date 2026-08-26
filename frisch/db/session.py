from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(database_url: str):
    if database_url.startswith("sqlite:///"):
        # Make sure the parent directory of a file-backed sqlite db exists
        # (the default dev database lives under ./.dev/).
        db_path = database_url[len("sqlite:///") :]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(database_url, pool_pre_ping=True, future=True)


def make_session_factory(database_url: str) -> sessionmaker[Session]:
    return sessionmaker(
        bind=make_engine(database_url), expire_on_commit=False, future=True
    )
