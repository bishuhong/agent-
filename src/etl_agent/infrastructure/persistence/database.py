"""Synchronous PostgreSQL unit-of-work infrastructure; never initializes schema."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Database:
    def __init__(self, database_url: str | Any, *, echo: bool = False) -> None:
        url = (
            database_url
            if isinstance(database_url, str) or hasattr(database_url, "get_secret_value")
            else database_url.database_url
        )
        if hasattr(url, "get_secret_value"):
            url = url.get_secret_value()
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        if not url.startswith("postgresql+psycopg://"):
            raise ValueError("A PostgreSQL psycopg database URL is required")
        self.engine: Engine = create_engine(
            url, echo=echo, pool_pre_ping=True, connect_args={"options": "-c timezone=UTC"}
        )
        self.session_factory: sessionmaker[Session] = sessionmaker(
            bind=self.engine, expire_on_commit=False, autoflush=False
        )
        self.sessionmaker = self.session_factory

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self.session_factory() as session:
            try:
                yield session
            except BaseException:
                session.rollback()
                raise

    def dispose(self) -> None:
        self.engine.dispose()
