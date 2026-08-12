"""Punctul de intrare al API-ului: creeaza tabelele, incarca resursele partajate,
monteaza router-ele si traduce `DomainError` in HTTP 422.

Contractul: `DomainError` (refuz motivat de domeniu) devine intotdeauna 422 cu
corpul `{"code", "message", "field"}` — niciodata un 500. Agentul vocal
povesteste acest mesaj clientului, nu il inventeaza.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from packages.domain.catalog import load_catalog
from packages.domain.config import load_kitchen_config, load_zone_config
from packages.domain.errors import DomainError
from packages.env import load_env

from . import db, events, geocoding
from .routes_menu import router as menu_router
from .routes_orders import router as orders_router
from .routes_session import router as session_router
from .sessions import SessionStore

_WEB_DIR = Path(__file__).resolve().parents[2] / "apps" / "web"

# Inainte de orice `os.environ.get` de mai jos: `DATABASE_URL`, `ENABLE_DOCS` si
# cheile de provider stau in `.env`, iar mediul real are prioritate peste el.
load_env()


def _init_state(app: FastAPI) -> None:
    """Creeaza tabelele si (re)incarca resursele partajate pe `app.state`.

    Apelata si la import (ca API-ul sa functioneze chiar fara ASGI lifespan,
    ex. un `TestClient(app)` folosit fara `with`), si din `lifespan` la
    fiecare startup real — asta reface DB-ul si sesiunile pe `DATABASE_URL`
    curent, esential pentru izolarea testelor (`tmp_path` + monkeypatch).
    """
    db.create_db_and_tables()
    app.state.catalog = load_catalog()
    app.state.zone = load_zone_config()
    app.state.kitchen = load_kitchen_config()
    app.state.addresses = geocoding.load_fixture()
    app.state.sessions = SessionStore()
    app.state.events = events.EventHub()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _init_state(app)
    yield


#: `/docs`, `/redoc` si `/openapi.json` sunt publice implicit in FastAPI — expun
#: schema completa a API-ului (rute, campuri, exemple). Dezactivate implicit;
#: activate explicit doar cand e nevoie de ele (dezvoltare/depanare).
_docs_enabled = os.environ.get("ENABLE_DOCS") == "1"

app = FastAPI(
    title="Pizzeria Punto API",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)
_init_state(app)

app.include_router(menu_router)
app.include_router(session_router)
app.include_router(orders_router)


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=422, content=exc.issue.model_dump())


@app.websocket("/ws/orders")
async def orders_ws(websocket: WebSocket) -> None:
    await events.handle_connection(websocket, websocket.app.state.events)


# Directorul e momentan gol (frontend-ul dashboard-urilor vine mai tarziu); montat
# tolerant, ca sa nu pice startup-ul daca directorul lipseste sau e vid.
app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True, check_dir=False), name="web")
