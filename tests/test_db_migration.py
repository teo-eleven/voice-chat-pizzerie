"""Aducerea la zi a unei baze scrise înainte de numărul zilnic de comandă.

Proiectul n-are unealtă de migrări, iar `create_all` nu adaugă coloane pe un tabel
care există deja: fără pasul din `db.create_db_and_tables`, prima interogare după
actualizare ar cădea cu „no such column" pe orice bază cu comenzi în ea.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, create_engine, select

from apps.api import db
from apps.api.tables import OrderRow

#: Coloanele de enum țin numele membrului („NEW"), nu valoarea lui („new") — așa le
#: scrie SQLAlchemy, deci așa arată și o bază veche reală.
#: Tabelul așa cum arăta înainte de `business_date` și `daily_number`.
_OLD_SCHEMA = """
CREATE TABLE orderrow (
    id VARCHAR NOT NULL PRIMARY KEY,
    status VARCHAR NOT NULL,
    fulfillment VARCHAR NOT NULL,
    payment VARCHAR NOT NULL,
    idempotency_key VARCHAR NOT NULL UNIQUE,
    created_at DATETIME NOT NULL,
    eta_min INTEGER NOT NULL,
    eta_max INTEGER NOT NULL,
    allergy_note VARCHAR,
    cart_json VARCHAR NOT NULL,
    address_json VARCHAR,
    contact_json VARCHAR NOT NULL
)
"""

_OLD_ROW = (
    "INSERT INTO orderrow VALUES "
    "(:id, 'NEW', 'PICKUP', 'CASH', :key, :created_at, 25, 35, NULL, '{}', NULL, '{}')"
)


@pytest.fixture
def old_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """O bază în formatul vechi, cu trei comenzi din două zile diferite."""
    db_path = tmp_path / "vechi.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.execute(text(_OLD_SCHEMA))
        for order_id, moment in (
            ("CMD-0001", datetime(2026, 8, 13, 10, 0, tzinfo=UTC)),
            ("CMD-0002", datetime(2026, 8, 13, 18, 0, tzinfo=UTC)),
            ("CMD-0003", datetime(2026, 8, 14, 9, 0, tzinfo=UTC)),
        ):
            connection.execute(
                text(_OLD_ROW), {"id": order_id, "key": order_id, "created_at": moment}
            )
    engine.dispose()

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    db._engine_cache.clear()
    return db_path


def test_adds_the_missing_columns(old_database: Path):
    # Act
    db.create_db_and_tables()

    # Assert
    columns = {column["name"] for column in inspect(db.get_engine()).get_columns("orderrow")}
    assert {"business_date", "daily_number"} <= columns


def test_existing_orders_keep_their_data(old_database: Path):
    db.create_db_and_tables()

    with Session(db.get_engine()) as session:
        ids = {row.id for row in session.exec(select(OrderRow)).all()}

    assert ids == {"CMD-0001", "CMD-0002", "CMD-0003"}


def test_old_orders_get_a_number_per_day_in_placement_order(old_database: Path):
    db.create_db_and_tables()

    with Session(db.get_engine()) as session:
        rows = {
            row.id: (row.business_date, row.daily_number)
            for row in session.exec(select(OrderRow)).all()
        }

    # Cele două din 13 august se numerotează 1 și 2; ziua următoare repornește de la 1.
    assert rows["CMD-0001"] == ("2026-08-13", 1)
    assert rows["CMD-0002"] == ("2026-08-13", 2)
    assert rows["CMD-0003"] == ("2026-08-14", 1)


def test_running_twice_changes_nothing(old_database: Path):
    db.create_db_and_tables()
    with Session(db.get_engine()) as session:
        before = {row.id: row.daily_number for row in session.exec(select(OrderRow)).all()}

    db.create_db_and_tables()

    with Session(db.get_engine()) as session:
        after = {row.id: row.daily_number for row in session.exec(select(OrderRow)).all()}
    assert before == after


def test_a_fresh_database_needs_no_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'noua.db'}")
    db._engine_cache.clear()

    db.create_db_and_tables()

    columns = {column["name"] for column in inspect(db.get_engine()).get_columns("orderrow")}
    assert {"business_date", "daily_number"} <= columns


def test_backfill_resumes_after_an_interrupted_migration(old_database: Path):
    """Scenariul care lasă comenzile fără număr pentru totdeauna, dacă nu e tratat.

    Coloanele se adaugă într-o tranzacție proprie, care face commit. Dacă procesul
    moare fix după ea, a doua pornire vede coloanele deja prezente — iar dacă
    completarea ar depinde de faptul că tocmai s-au adăugat coloane, n-ar mai rula
    niciodată, iar acele comenzi ar rămâne „numărul comenzii 0”.
    """
    # Arrange — simulăm exact starea de după întrerupere: coloane adăugate, date lipsă.
    db._add_missing_order_columns(db.get_engine())

    # Act — pornirea următoare.
    db.create_db_and_tables()

    # Assert
    with Session(db.get_engine()) as session:
        numbers = {row.id: row.daily_number for row in session.exec(select(OrderRow)).all()}
    assert numbers == {"CMD-0001": 1, "CMD-0002": 2, "CMD-0003": 1}


def test_creates_the_unique_index_on_day_and_number(old_database: Path):
    db.create_db_and_tables()

    indexes = {index["name"] for index in inspect(db.get_engine()).get_indexes("orderrow")}

    assert db._DAILY_NUMBER_INDEX in indexes


def test_the_index_refuses_two_orders_with_the_same_number_on_a_day(old_database: Path):
    # Plasa de siguranță pentru cazul în care blocarea din proces e ocolită
    # (de exemplu la o pornire cu mai mulți workeri).
    db.create_db_and_tables()

    with Session(db.get_engine()) as session:
        clone = session.get(OrderRow, "CMD-0003")
        assert clone is not None
        duplicate = OrderRow(
            **{**clone.model_dump(), "id": "CMD-9999", "idempotency_key": "CMD-9999"}
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.commit()


def test_a_database_with_duplicate_numbers_fails_loudly(old_database: Path):
    # Arrange — o bază deja stricată: două comenzi cu același număr în aceeași zi.
    db._add_missing_order_columns(db.get_engine())
    with Session(db.get_engine()) as session:
        for order_id in ("CMD-0001", "CMD-0002"):
            row = session.get(OrderRow, order_id)
            assert row is not None
            row.business_date = "2026-08-13"
            row.daily_number = 1
            session.add(row)
        session.commit()

    # Act / Assert — nu renumerotăm de capul nostru, spunem ce e de reparat.
    with pytest.raises(RuntimeError, match="acelasi numar"):
        db.create_db_and_tables()
