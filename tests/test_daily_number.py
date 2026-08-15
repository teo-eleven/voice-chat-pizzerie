"""Numărul comenzii, cel pe care îl spune omul la telefon: repornește de la 1 zilnic.

Numărul zilnic NU e cheie: se repetă în fiecare zi. Identitatea comenzii rămâne
`id`-ul intern, unic peste tot istoricul; numărul zilnic e doar pentru oameni —
„numărul comenzii 7" e ceva ce clientul și bucătarul pot rosti și reține.

Ziua se rupe după ceasul local al pizzeriei (Europe/București), nu după UTC:
altfel, comenzile dintre miezul nopții și ora 3 dimineața ar primi numere din
ziua precedentă.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlmodel import Session, SQLModel, create_engine

from apps.api.tables import OrderRow, business_date_of, next_daily_number
from tests.test_api import _ready_delivery_session, _ready_pickup_session


def _memory_session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _row(order_id: str, business_date: str, daily_number: int) -> OrderRow:
    return OrderRow(
        id=order_id,
        status="new",
        fulfillment="pickup",
        payment="cash",
        idempotency_key=order_id,
        created_at=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
        business_date=business_date,
        daily_number=daily_number,
        eta_min=25,
        eta_max=35,
        cart_json="{}",
        contact_json="{}",
    )


class TestBusinessDate:
    def test_uses_the_local_day_not_the_utc_day(self):
        # Arrange — 00:30 în București e încă 21:30 UTC în ziua precedentă (vara).
        just_after_midnight_local = datetime(2026, 8, 13, 21, 30, tzinfo=UTC)

        # Act
        business_date = business_date_of(just_after_midnight_local)

        # Assert
        assert business_date == "2026-08-14"

    def test_two_moments_of_the_same_local_day_share_a_date(self):
        morning = datetime(2026, 8, 14, 5, 0, tzinfo=UTC)
        evening = datetime(2026, 8, 14, 20, 0, tzinfo=UTC)

        assert business_date_of(morning) == business_date_of(evening) == "2026-08-14"

    def test_a_naive_timestamp_is_read_as_utc(self):
        # Rândurile vechi din SQLite pot veni fără fus orar; nu are voie să crape.
        assert business_date_of(datetime(2026, 8, 14, 12, 0)) == "2026-08-14"


class TestNextDailyNumber:
    def test_the_first_order_of_the_day_is_number_one(self):
        with _memory_session() as session:
            assert next_daily_number(session, "2026-08-14") == 1

    def test_the_number_grows_within_the_same_day(self):
        with _memory_session() as session:
            session.add(_row("CMD-0001", "2026-08-14", 1))
            session.add(_row("CMD-0002", "2026-08-14", 2))
            session.commit()

            assert next_daily_number(session, "2026-08-14") == 3

    def test_a_new_day_starts_over_at_one(self):
        # Arrange — ziua precedentă a ajuns la 37 de comenzi.
        with _memory_session() as session:
            for number in range(1, 38):
                session.add(_row(f"CMD-{number:04d}", "2026-08-13", number))
            session.commit()

            # Act
            first_of_new_day = next_daily_number(session, "2026-08-14")

            # Assert
            assert first_of_new_day == 1

    def test_a_deleted_order_does_not_recycle_its_number(self):
        # Numărul vine din maxim, nu din numărul de rânduri: după o ștergere, două
        # comenzi din aceeași zi ar ajunge altfel cu același număr rostit.
        with _memory_session() as session:
            session.add(_row("CMD-0001", "2026-08-14", 1))
            session.add(_row("CMD-0002", "2026-08-14", 2))
            session.commit()
            session.delete(session.get(OrderRow, "CMD-0001"))
            session.commit()

            assert next_daily_number(session, "2026-08-14") == 3

    def test_days_are_counted_separately(self):
        with _memory_session() as session:
            session.add(_row("CMD-0001", "2026-08-13", 1))
            session.add(_row("CMD-0002", "2026-08-14", 1))
            session.add(_row("CMD-0003", "2026-08-14", 2))
            session.commit()

            assert next_daily_number(session, "2026-08-13") == 2
            assert next_daily_number(session, "2026-08-14") == 3


class TestDailyNumberOverTheApi:
    """Numărul trebuie să ajungă până pe ecran, nu doar în baza de date."""

    def test_the_first_order_of_a_fresh_day_is_number_one(self, client):
        sid = _ready_pickup_session(client)

        placed = client.post(f"/api/sessions/{sid}/place", json={"idempotency_key": "n-1"})

        assert placed.status_code == 200
        assert placed.json()["daily_number"] == 1

    def test_the_second_order_is_number_two(self, client):
        first = _ready_pickup_session(client)
        client.post(f"/api/sessions/{first}/place", json={"idempotency_key": "n-1"})
        second = _ready_pickup_session(client)

        placed = client.post(f"/api/sessions/{second}/place", json={"idempotency_key": "n-2"})

        assert placed.json()["daily_number"] == 2

    def test_the_kitchen_screen_receives_the_number(self, client):
        # Ecranul de bucătărie primește un model redus (`KitchenOrderOut`); numărul
        # trebuie să fie printre câmpurile păstrate, altfel bucătarul n-are ce striga.
        sid = _ready_pickup_session(client)
        client.post(f"/api/sessions/{sid}/place", json={"idempotency_key": "k-1"})

        orders = client.get("/api/orders", params={"view": "kitchen"}).json()

        assert [order["daily_number"] for order in orders] == [1]

    def test_the_driver_screen_receives_the_number(self, client):
        sid = _ready_delivery_session(client)
        placed = client.post(f"/api/sessions/{sid}/place", json={"idempotency_key": "d-1"})
        order_id = placed.json()["id"]
        for status in ("in_kitchen", "ready"):
            client.post(f"/api/orders/{order_id}/status", json={"status": status})

        orders = client.get("/api/orders", params={"view": "driver"}).json()

        assert [order["daily_number"] for order in orders] == [1]

    def test_a_retried_placement_keeps_the_same_number(self, client):
        # Idempotența nu are voie să consume un număr în plus.
        sid = _ready_pickup_session(client)
        first = client.post(f"/api/sessions/{sid}/place", json={"idempotency_key": "same"})
        second = client.post(f"/api/sessions/{sid}/place", json={"idempotency_key": "same"})

        assert first.json()["daily_number"] == second.json()["daily_number"] == 1


class TestConcurrentPlacement:
    def test_two_orders_placed_at_the_same_time_get_different_numbers(self, client):
        """Două plasări simultane nu au voie să primească același număr rostit.

        Alocarea trece prin `ORDER_ID_LOCK`, iar `place_order` rulează efectiv pe
        thread-uri (vezi `run_in_threadpool` din rută), deci cursa e reproductibilă.
        """
        # Arrange — două sesiuni gata de plasare.
        sessions = [_ready_pickup_session(client) for _ in range(2)]

        def place(index: int) -> dict[str, object]:
            response = client.post(
                f"/api/sessions/{sessions[index]}/place",
                json={"idempotency_key": f"concurrent-{index}"},
            )
            assert response.status_code == 200
            return dict(response.json())

        # Act
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(place, 0), executor.submit(place, 1)]
            placed = [future.result() for future in futures]

        # Assert — id-uri distincte ȘI numere rostite distincte, consecutive de la 1.
        assert len({order["id"] for order in placed}) == 2
        assert sorted(order["daily_number"] for order in placed) == [1, 2]
