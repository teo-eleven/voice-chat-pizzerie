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
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, col, create_engine, select

from .tables import OrderRow, business_date_of

_DEFAULT_DATABASE_URL = "sqlite:///./pizza_punto.db"

_engine_cache: dict[str, Engine] = {}

#: Cat asteapta o conexiune SQLite ca baza sa se elibereze inainte sa renunte.
SQLITE_BUSY_TIMEOUT_SECONDS = 15.0


def _current_database_url() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DATABASE_URL)


def get_engine() -> Engine:
    """Motorul curent, dupa `DATABASE_URL` din mediu. Creat o singura data per URL."""
    url = _current_database_url()
    engine = _engine_cache.get(url)
    if engine is None:
        # `timeout`: la o baza ocupata (doua porniri simultane, o migrare in curs),
        # SQLite asteapta eliberarea in loc sa cada imediat cu „database is locked".
        connect_args = (
            {"check_same_thread": False, "timeout": SQLITE_BUSY_TIMEOUT_SECONDS}
            if url.startswith("sqlite")
            else {}
        )
        engine = create_engine(url, connect_args=connect_args)
        _engine_cache[url] = engine
    return engine


def create_db_and_tables() -> None:
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    _add_missing_order_columns(engine)
    # Pasii de dupa nu depind de faptul ca s-au adaugat coloane acum: fiecare isi
    # verifica singur daca mai are ceva de facut. Altfel, o pornire intrerupta intre
    # `ALTER TABLE` si completare ar lasa comenzile vechi fara numar pentru totdeauna
    # — a doua pornire ar vedea coloanele deja prezente si n-ar mai relua nimic.
    _backfill_business_dates(engine)
    _add_daily_number_unique_index(engine)


#: Coloanele adaugate dupa ce existau deja baze cu comenzi in ele, cu tipul lor SQL
#: si valoarea implicita pentru randurile vechi. `create_all` creeaza doar tabele
#: care lipsesc — pe unul existent nu adauga coloane, iar prima interogare ar cadea
#: cu „no such column". Proiectul n-are unealta de migrari; atat timp cat pasii sunt
#: adaugari de coloane, lista asta tine locul uneia.
_ADDED_ORDER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("business_date", "VARCHAR DEFAULT ''"),
    ("daily_number", "INTEGER DEFAULT 0"),
)


def _add_missing_order_columns(engine: Engine) -> None:
    """Aduce tabelul de comenzi la zi, fara sa atinga randurile existente.

    Idempotent: se cheama la fiecare pornire si nu face nimic daca totul e la locul lui.
    """
    inspector = inspect(engine)
    if "orderrow" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("orderrow")}
    missing = [(name, sql) for name, sql in _ADDED_ORDER_COLUMNS if name not in existing]
    if not missing:
        return
    with engine.begin() as connection:
        for name, sql_type in missing:
            connection.execute(text(f"ALTER TABLE orderrow ADD COLUMN {name} {sql_type}"))


#: Numele indexului care garanteaza, la nivel de baza, ca doua comenzi din aceeasi
#: zi nu pot purta acelasi numar rostit.
_DAILY_NUMBER_INDEX = "ix_orderrow_business_date_daily_number"


def _add_daily_number_unique_index(engine: Engine) -> None:
    """Face imposibil, la nivel de baza, ca doua comenzi din aceeasi zi sa aiba
    acelasi numar rostit.

    `ORDER_ID_LOCK` ajunge cat timp aplicatia ruleaza intr-un singur proces, dar un
    `threading.Lock` nu trece dincolo de proces: pornita cu `--workers 2`, aplicatia
    ar striga acelasi numar de doua ori, tacut. Cu indexul, a doua inserare pica cu
    `IntegrityError`, iar plasarea reincearca — exact tratamentul pe care il primeste
    deja o coliziune de id.

    Se cheama dupa completare, cand nu mai exista randuri vechi cu („”, 0).
    """
    inspector = inspect(engine)
    if "orderrow" not in inspector.get_table_names():
        return
    if any(index["name"] == _DAILY_NUMBER_INDEX for index in inspector.get_indexes("orderrow")):
        return
    statement = text(
        f"CREATE UNIQUE INDEX {_DAILY_NUMBER_INDEX} "
        "ON orderrow (business_date, daily_number)"
    )
    try:
        with engine.begin() as connection:
            connection.execute(statement)
    except IntegrityError as exc:
        # Baza contine deja doua comenzi cu acelasi numar in aceeasi zi. Nu inventam
        # o renumerotare pe cont propriu — se spune pe fata, cu numele indexului, ca
        # sa poata fi reparat manual.
        raise RuntimeError(
            "Baza de date contine comenzi diferite cu acelasi numar in aceeasi zi, "
            f"deci indexul „{_DAILY_NUMBER_INDEX}” nu poate fi creat. Verificati "
            "randurile duplicate din `orderrow` (business_date, daily_number)."
        ) from exc


def _backfill_business_dates(engine: Engine) -> None:
    """Completeaza ziua si numarul pentru comenzile scrise inainte de aceste coloane.

    Fara asta, comenzile vechi ar aparea pe ecrane ca „numarul comenzii 0”, iar
    numerotarea zilei curente ar putea reporni peste comenzi deja plasate azi.
    Numerele se dau in ordinea plasarii, separat pe fiecare zi.

    Idempotent si reluabil: cauta randurile ramase fara zi, nu se bazeaza pe faptul
    ca migrarea coloanelor tocmai a rulat.
    """
    with Session(engine) as session:
        statement = (
            select(OrderRow)
            .where(col(OrderRow.business_date) == "")
            .order_by(col(OrderRow.created_at))
        )
        rows = session.exec(statement).all()
        if not rows:
            return
        counters: dict[str, int] = {}
        for row in rows:
            business_date = business_date_of(row.created_at)
            counters[business_date] = counters.get(business_date, 0) + 1
            row.business_date = business_date
            row.daily_number = counters[business_date]
            session.add(row)
        session.commit()


def get_session() -> Generator[Session, None, None]:
    """Dependency FastAPI: o sesiune DB per cerere, inchisa automat la final."""
    with Session(get_engine()) as session:
        yield session


#: Alias de tip pentru injectarea sesiunii DB, fara `Depends()` ca valoare implicita.
SessionDep = Annotated[Session, Depends(get_session)]
