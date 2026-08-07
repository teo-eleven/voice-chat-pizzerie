"""Persistenta pe SQLite via SQLModel.

SQLite e sursa de adevar pentru comenzi: dashboard-urile isi reconstruiesc
starea din DB la reload, nu din evenimente WebSocket pierdute.

Motorul e memorat per URL (nu per proces): asta permite testelor sa schimbe
`DATABASE_URL` prin monkeypatch, fara reload de modul, si fara sa recream
motorul la fiecare cerere in productie, unde URL-ul e stabil.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

_DEFAULT_DATABASE_URL = "sqlite:///./pizza_punto.db"

_engine_cache: dict[str, Engine] = {}


def _current_database_url() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DATABASE_URL)


def get_engine() -> Engine:
    """Motorul curent, dupa `DATABASE_URL` din mediu. Creat o singura data per URL."""
    url = _current_database_url()
    engine = _engine_cache.get(url)
    if engine is None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        engine = create_engine(url, connect_args=connect_args)
        _engine_cache[url] = engine
    return engine


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(get_engine())


def get_session() -> Generator[Session, None, None]:
    """Dependency FastAPI: o sesiune DB per cerere, inchisa automat la final."""
    with Session(get_engine()) as session:
        yield session


#: Alias de tip pentru injectarea sesiunii DB, fara `Depends()` ca valoare implicita.
SessionDep = Annotated[Session, Depends(get_session)]
