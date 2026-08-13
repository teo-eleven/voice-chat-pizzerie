"""Căile de concurență din `apps/api/placement.py`.

Sunt exact ramurile pe care docstring-urile modulului le descriu ca fiind critice —
supraîncărcarea bucătăriei, recuperarea după o coliziune pe cheia de idempotență și
epuizarea reîncercărilor — și singurele rămase neacoperite de suită. Un bug real
(al doilea eveniment `order_created` emis pentru o comandă deja anunțată) a trăit
aici tocmai pentru că nu era niciun test pe ele.

Toate se declanșează prin `monkeypatch`: sunt curse care, în condiții normale, cer
două cereri suprapuse la milisecundă.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

import apps.api.placement as placement_module
from packages.domain.models import KitchenConfig

from .test_api import _ready_delivery_session


class TestKitchenOverload:
    def test_place_is_refused_when_eta_exceeds_what_kitchen_can_promise(
        self, client: TestClient
    ):
        # Arrange: bucătărie care nu poate promite mai mult de un minut, deci orice
        # comandă reală o depășește. Plafonul e config, nu regulă de cod.
        sid = _ready_delivery_session(client)
        real = client.app.state.kitchen
        client.app.state.kitchen = KitchenConfig(
            oven_slots=real.oven_slots,
            order_overhead_minutes=real.order_overhead_minutes,
            safety_buffer_minutes=real.safety_buffer_minutes,
            quote_rounding_minutes=real.quote_rounding_minutes,
            quote_window_minutes=real.quote_window_minutes,
            max_promisable_minutes=1,
            delivery_drive_minutes=real.delivery_drive_minutes,
        )

        # Act
        response = client.post(
            f"/api/sessions/{sid}/place", json={"idempotency_key": "overload-1"}
        )

        # Assert
        assert response.status_code == 422
        assert response.json()["code"] == "kitchen_overloaded"


class TestIdempotencyKeyCollisionRecovery:
    """Două cereri cu aceeași cheie, ambele trecute de verificarea inițială.

    A doua pierde cursa la `commit()`, prinde `IntegrityError` pe constrângerea unică
    de `idempotency_key` și recuperează rândul deja scris de prima. Comanda NU se
    dublează în bază — dar nici nu trebuie anunțată a doua oară pe `/ws/orders`.
    """

    @staticmethod
    def _blind_first_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
        """Face prima căutare după cheie să nu găsească nimic.

        Așa se simulează fereastra reală: ambele cereri verifică înainte ca vreuna să
        fi făcut commit, deci ambele cred că sunt prima.
        """
        real_lookup = placement_module._find_by_idempotency_key
        state = {"calls": 0}

        def _fake(db_session, idempotency_key):
            state["calls"] += 1
            if state["calls"] == 1:
                return None
            return real_lookup(db_session, idempotency_key)

        monkeypatch.setattr(placement_module, "_find_by_idempotency_key", _fake)

    def test_returns_the_existing_order_instead_of_creating_a_second_one(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        # Arrange: o comandă deja plasată cu cheia „race-1"
        first_sid = _ready_delivery_session(client)
        first = client.post(
            f"/api/sessions/{first_sid}/place", json={"idempotency_key": "race-1"}
        )
        assert first.status_code == 200
        self._blind_first_lookup(monkeypatch)

        # Act: a doua cerere, aceeași cheie, dar oarbă la prima căutare
        second_sid = _ready_delivery_session(client)
        second = client.post(
            f"/api/sessions/{second_sid}/place", json={"idempotency_key": "race-1"}
        )

        # Assert: același id, o singură comandă în bucătărie
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        kitchen = client.get("/api/orders", params={"view": "kitchen"}).json()
        assert [order["id"] for order in kitchen] == [first.json()["id"]]

    def test_does_not_broadcast_a_second_order_created_event(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        # Arrange
        first_sid = _ready_delivery_session(client)
        first = client.post(
            f"/api/sessions/{first_sid}/place", json={"idempotency_key": "race-2"}
        )
        assert first.status_code == 200
        self._blind_first_lookup(monkeypatch)
        second_sid = _ready_delivery_session(client)

        # Act: ascultăm pe canalul dashboard-urilor în timpul celei de-a doua cereri,
        # apoi provocăm un eveniment despre care știm sigur că trebuie să sosească.
        with client.websocket_connect("/ws/orders") as websocket:
            second = client.post(
                f"/api/sessions/{second_sid}/place", json={"idempotency_key": "race-2"}
            )
            client.post(f"/api/orders/{first.json()['id']}/status", json={"status": "in_kitchen"})
            event = websocket.receive_json()

        # Assert: primul eveniment de pe fir e schimbarea de status, nu un al doilea
        # `order_created` — recuperarea idempotentă nu anunță nimic.
        assert second.status_code == 200
        assert event["type"] == "order_status_changed"


class TestRetriesExhausted:
    def test_persistent_id_collision_stops_after_max_attempts(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        # Arrange: primul id e ocupat, iar generatorul îl întoarce la nesfârșit.
        # Cheia de idempotență diferă, deci recuperarea idempotentă nu intervine —
        # rămâne doar coliziunea pe cheia primară, care nu se mai poate rezolva.
        first_sid = _ready_delivery_session(client)
        taken_id = client.post(
            f"/api/sessions/{first_sid}/place", json={"idempotency_key": "exhaust-1"}
        ).json()["id"]
        monkeypatch.setattr(placement_module, "next_order_id", lambda _db: taken_id)

        # Act / Assert: se ridică ultima eroare de integritate, după cele trei încercări
        second_sid = _ready_delivery_session(client)
        with pytest.raises(IntegrityError):
            client.post(
                f"/api/sessions/{second_sid}/place", json={"idempotency_key": "exhaust-2"}
            )
