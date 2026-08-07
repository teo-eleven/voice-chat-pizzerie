"""Teste de integrare pentru stratul API (apps/api).

`TestClient` peste DB pe fisier temporar (per test, prin `tmp_path` +
monkeypatch pe `DATABASE_URL`), fara retea. Domeniul (`packages/domain`) e
deja testat separat — aici verificam doar orchestrarea HTTP peste el:
traducerea `DomainError` -> 422, persistenta, idempotenta, vizibilitatea
comenzilor pe dashboard-uri.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

import apps.api.routes_session as routes_session_module
from apps.api.db import get_engine
from apps.api.main import app
from apps.api.sessions import SessionStore
from apps.api.tables import OrderRow
from packages.domain.limits import MAX_ADDRESS_TEXT_LEN, MAX_QTY_PER_LINE


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Fiecare test primeste un fisier SQLite propriu, izolat de restul suitei.
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    with TestClient(app) as test_client:
        yield test_client


def _create_session(client: TestClient) -> str:
    response = client.post("/api/sessions")
    return response.json()["session_id"]


def _add_pizza(
    client: TestClient, sid: str, product_id: str = "PZ-002", qty: int = 2, size_code: str = "large"
) -> None:
    client.post(
        f"/api/sessions/{sid}/items",
        json={"product_id": product_id, "qty": qty, "size_code": size_code},
    )


def _ready_delivery_session(client: TestClient) -> str:
    """Sesiune completa de livrare: cos peste minim, adresa in zona, contact, plata."""
    sid = _create_session(client)
    _add_pizza(client, sid)
    client.put(f"/api/sessions/{sid}/fulfillment", json={"fulfillment": "delivery"})
    client.post(f"/api/sessions/{sid}/address", json={"text": "Aleea Nucsoara 4"})
    client.put(f"/api/sessions/{sid}/contact", json={"phone": "0711111111", "name": "Ana"})
    client.put(f"/api/sessions/{sid}/payment", json={"payment": "cash"})
    return sid


def _ready_pickup_session(client: TestClient) -> str:
    """Sesiune completa de ridicare: fara adresa, contact si plata setate."""
    sid = _create_session(client)
    client.put(f"/api/sessions/{sid}/fulfillment", json={"fulfillment": "pickup"})
    _add_pizza(client, sid, qty=1)
    client.put(f"/api/sessions/{sid}/contact", json={"phone": "0722222222", "name": "Ion"})
    client.put(f"/api/sessions/{sid}/payment", json={"payment": "cash"})
    return sid


class TestDeliveryFlow:
    def test_full_delivery_flow_places_order_with_eta(self, client: TestClient):
        # Arrange
        sid = _ready_delivery_session(client)

        # Act
        summary = client.get(f"/api/sessions/{sid}/summary")
        placed = client.post(f"/api/sessions/{sid}/place", json={"idempotency_key": "delivery-1"})

        # Assert
        assert summary.status_code == 200
        assert summary.json()["spoken_text"]
        assert placed.status_code == 200
        body = placed.json()
        assert body["status"] == "new"
        assert body["eta"]["min_minutes"] > 0
        assert body["eta"]["max_minutes"] >= body["eta"]["min_minutes"]


class TestPickupFlow:
    def test_pickup_order_succeeds_without_address_and_is_hidden_from_driver_view(
        self, client: TestClient
    ):
        # Arrange
        sid = _ready_pickup_session(client)

        # Act
        placed = client.post(f"/api/sessions/{sid}/place", json={"idempotency_key": "pickup-1"})
        driver_view = client.get("/api/orders", params={"view": "driver"})

        # Assert
        assert placed.status_code == 200
        order_id = placed.json()["id"]
        assert all(order["id"] != order_id for order in driver_view.json())


class TestIdempotency:
    def test_same_idempotency_key_twice_returns_same_order_without_duplicate(
        self, client: TestClient
    ):
        # Arrange
        sid = _ready_delivery_session(client)

        # Act
        first = client.post(f"/api/sessions/{sid}/place", json={"idempotency_key": "dup-1"})
        second = client.post(f"/api/sessions/{sid}/place", json={"idempotency_key": "dup-1"})

        # Assert
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
        all_orders = client.get("/api/orders", params={"view": "kitchen"}).json()
        matching = [order for order in all_orders if order["id"] == first.json()["id"]]
        assert len(matching) == 1


class TestEmptyCartRejection:
    def test_place_on_empty_cart_returns_422_empty_cart(self, client: TestClient):
        # Arrange: sesiune fara produse, dar cu restul completat
        sid = _create_session(client)
        client.put(f"/api/sessions/{sid}/fulfillment", json={"fulfillment": "pickup"})
        client.put(f"/api/sessions/{sid}/contact", json={"phone": "0700000000"})
        client.put(f"/api/sessions/{sid}/payment", json={"payment": "cash"})

        # Act
        response = client.post(f"/api/sessions/{sid}/place", json={"idempotency_key": "empty-1"})

        # Assert
        assert response.status_code == 422
        assert response.json()["code"] == "empty_cart"


class TestBelowMinimumRejection:
    def test_place_delivery_below_minimum_returns_422_below_minimum_order(self, client: TestClient):
        # Arrange: adresa in zona, dar cosul (o apa, 5 lei) e sub minimul de livrare
        sid = _create_session(client)
        client.post(f"/api/sessions/{sid}/address", json={"text": "Aleea Nucsoara 4"})
        client.post(f"/api/sessions/{sid}/items", json={"product_id": "BT-006", "qty": 1})
        client.put(f"/api/sessions/{sid}/contact", json={"phone": "0700000000"})
        client.put(f"/api/sessions/{sid}/payment", json={"payment": "cash"})

        # Act
        response = client.post(f"/api/sessions/{sid}/place", json={"idempotency_key": "min-1"})

        # Assert
        assert response.status_code == 422
        assert response.json()["code"] == "below_minimum_order"


class TestMissingAddressRejection:
    def test_place_delivery_without_address_returns_422(self, client: TestClient):
        # Arrange: livrare (implicit), cos peste minim, dar fara adresa setata
        sid = _create_session(client)
        _add_pizza(client, sid)
        client.put(f"/api/sessions/{sid}/contact", json={"phone": "0700000000"})
        client.put(f"/api/sessions/{sid}/payment", json={"payment": "cash"})

        # Act
        response = client.post(f"/api/sessions/{sid}/place", json={"idempotency_key": "addr-1"})

        # Assert
        assert response.status_code == 422
        assert response.json()["code"] == "address_required"


class TestResolveAddress:
    def test_address_outside_zone_is_not_saved_on_session(self, client: TestClient):
        # Arrange
        sid = _create_session(client)

        # Act
        response = client.post(
            f"/api/sessions/{sid}/address", json={"text": "Strada Aviatorilor 10"}
        )
        session_state = client.get(f"/api/sessions/{sid}")

        # Assert
        assert response.status_code == 200
        assert response.json()["resolution"] == "out_of_zone"
        assert session_state.json()["address"] is None

    def test_ambiguous_street_returns_at_most_three_candidates(self, client: TestClient):
        # Arrange
        sid = _create_session(client)

        # Act
        response = client.post(
            f"/api/sessions/{sid}/address", json={"text": "Strada Trandafirilor 5"}
        )

        # Assert
        body = response.json()
        assert response.status_code == 200
        assert body["resolution"] == "ambiguous"
        assert 1 < len(body["candidates"]) <= 3

    def test_matching_address_is_saved_on_session(self, client: TestClient):
        # Arrange
        sid = _create_session(client)

        # Act
        response = client.post(f"/api/sessions/{sid}/address", json={"text": "Aleea Nucsoara 4"})
        session_state = client.get(f"/api/sessions/{sid}")

        # Assert
        assert response.json()["resolution"] == "ok"
        assert session_state.json()["address"]["street"] == "Aleea Nucșoara"


class TestAddItemValidation:
    def test_add_pizza_without_size_returns_422_size_required(self, client: TestClient):
        # Arrange
        sid = _create_session(client)

        # Act
        response = client.post(
            f"/api/sessions/{sid}/items", json={"product_id": "PZ-002", "qty": 1}
        )

        # Assert
        assert response.status_code == 422
        assert response.json()["code"] == "size_required"


class TestCartMutations:
    def test_update_item_changes_quantity_and_reprices_the_cart(self, client: TestClient):
        # Arrange
        sid = _create_session(client)
        added = client.post(
            f"/api/sessions/{sid}/items",
            json={"product_id": "PZ-001", "qty": 1, "size_code": "small"},
        )
        line_id = added.json()["lines"][0]["line_id"]

        # Act
        response = client.patch(f"/api/sessions/{sid}/items/{line_id}", json={"qty": 3})

        # Assert
        assert response.status_code == 200
        assert response.json()["lines"][0]["qty"] == 3
        assert response.json()["items_bani"] == 3 * added.json()["lines"][0]["unit_price_bani"]

    def test_remove_item_empties_the_cart(self, client: TestClient):
        # Arrange
        sid = _create_session(client)
        added = client.post(
            f"/api/sessions/{sid}/items",
            json={"product_id": "PZ-001", "qty": 1, "size_code": "small"},
        )
        line_id = added.json()["lines"][0]["line_id"]

        # Act
        response = client.delete(f"/api/sessions/{sid}/items/{line_id}")

        # Assert
        assert response.status_code == 200
        assert response.json()["lines"] == []


class TestOrderVisibility:
    def test_delivery_order_visible_to_kitchen_from_new_and_to_driver_only_from_ready(
        self, client: TestClient
    ):
        # Arrange
        sid = _ready_delivery_session(client)
        order_id = client.post(
            f"/api/sessions/{sid}/place", json={"idempotency_key": "vis-1"}
        ).json()["id"]

        # Act: comanda e NEW
        kitchen_response = client.get("/api/orders", params={"view": "kitchen"})
        driver_response = client.get("/api/orders", params={"view": "driver"})
        kitchen_ids = [order["id"] for order in kitchen_response.json()]
        driver_ids = [order["id"] for order in driver_response.json()]

        # Assert
        assert order_id in kitchen_ids
        assert order_id not in driver_ids

        # Act: avanseaza comanda pana la READY
        client.post(f"/api/orders/{order_id}/status", json={"status": "in_kitchen"})
        client.post(f"/api/orders/{order_id}/status", json={"status": "ready"})
        driver_ids_after_ready = [
            o["id"] for o in client.get("/api/orders", params={"view": "driver"}).json()
        ]

        # Assert
        assert order_id in driver_ids_after_ready


class TestOrderStatusTransitions:
    def test_invalid_transition_returns_422(self, client: TestClient):
        # Arrange: comanda proaspata, in starea NEW
        sid = _ready_delivery_session(client)
        order_id = client.post(
            f"/api/sessions/{sid}/place", json={"idempotency_key": "trans-1"}
        ).json()["id"]

        # Act: NEW -> READY nu e o tranzitie permisa direct
        response = client.post(f"/api/orders/{order_id}/status", json={"status": "ready"})

        # Assert
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_transition"

    def test_picked_up_on_delivery_order_returns_422_fulfillment_mismatch(self, client: TestClient):
        # Arrange: comanda de livrare, adusa in starea READY
        sid = _ready_delivery_session(client)
        order_id = client.post(
            f"/api/sessions/{sid}/place", json={"idempotency_key": "trans-2"}
        ).json()["id"]
        client.post(f"/api/orders/{order_id}/status", json={"status": "in_kitchen"})
        client.post(f"/api/orders/{order_id}/status", json={"status": "ready"})

        # Act: PICKED_UP e rezervat ridicarii, nu livrarii
        response = client.post(f"/api/orders/{order_id}/status", json={"status": "picked_up"})

        # Assert
        assert response.status_code == 422
        assert response.json()["code"] == "fulfillment_mismatch"


class TestAskedChecklist:
    def test_marks_category_as_asked(self, client: TestClient):
        # Arrange
        sid = _create_session(client)

        # Act
        response = client.put(f"/api/sessions/{sid}/asked/drink")

        # Assert
        assert response.status_code == 200
        assert response.json()["asked_flags"]["drink"] is True

    def test_rejects_category_outside_the_upsell_checklist(self, client: TestClient):
        # Arrange
        sid = _create_session(client)

        # Act: "pizza" nu face parte din checklist-ul de upsell (sauce/drink/dessert)
        response = client.put(f"/api/sessions/{sid}/asked/pizza")

        # Assert
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_asked_category"


class TestNotFound:
    def test_unknown_session_id_returns_404(self, client: TestClient):
        # Act
        response = client.get("/api/sessions/S999")

        # Assert
        assert response.status_code == 404

    def test_unknown_order_id_returns_404(self, client: TestClient):
        # Act
        response = client.get("/api/orders/CMD-9999")

        # Assert
        assert response.status_code == 404


class TestMenu:
    def test_search_by_query_returns_matching_products(self, client: TestClient):
        # Act
        response = client.get("/api/menu", params={"query": "capriciosa"})

        # Assert
        assert response.status_code == 200
        assert any(item["id"] == "PZ-002" for item in response.json())

    def test_filter_by_category_returns_only_that_category(self, client: TestClient):
        # Act
        response = client.get("/api/menu", params={"category": "drink"})

        # Assert
        assert response.status_code == 200
        assert response.json()
        assert all(item["category"] == "drink" for item in response.json())


class TestOrdersWebSocket:
    def test_broadcasts_order_created_event_on_place(self, client: TestClient):
        # Arrange
        sid = _ready_delivery_session(client)

        # Act
        with client.websocket_connect("/ws/orders") as websocket:
            placed = client.post(f"/api/sessions/{sid}/place", json={"idempotency_key": "ws-1"})
            event = websocket.receive_json()

        # Assert
        assert placed.status_code == 200
        assert event["type"] == "order_created"
        assert event["order_id"] == placed.json()["id"]


class TestConcurrentCartMutations:
    def test_parallel_add_item_calls_do_not_lose_either_product(self, client: TestClient):
        # Arrange: o singura sesiune, doua produse diferite adaugate simultan
        sid = _create_session(client)

        def add(product_id: str) -> None:
            response = client.post(
                f"/api/sessions/{sid}/items",
                json={"product_id": product_id, "qty": 1, "size_code": "small"},
            )
            assert response.status_code == 200

        # Act
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(add, "PZ-001"), executor.submit(add, "PZ-002")]
            for future in futures:
                future.result()

        # Assert: ambele linii sunt in cos — nicio scriere n-a fost pierduta
        cart = client.get(f"/api/sessions/{sid}").json()["cart"]
        product_ids = sorted(line["product_id"] for line in cart["lines"])
        assert product_ids == ["PZ-001", "PZ-002"]


class TestInputLimits:
    def test_qty_above_limit_returns_422(self, client: TestClient):
        # Arrange
        sid = _create_session(client)

        # Act
        response = client.post(
            f"/api/sessions/{sid}/items",
            json={
                "product_id": "PZ-001",
                "qty": MAX_QTY_PER_LINE + 1,
                "size_code": "small",
            },
        )

        # Assert
        assert response.status_code == 422

    def test_address_text_above_max_length_returns_422(self, client: TestClient):
        # Arrange
        sid = _create_session(client)
        long_text = "a" * (MAX_ADDRESS_TEXT_LEN + 1)

        # Act
        response = client.post(f"/api/sessions/{sid}/address", json={"text": long_text})

        # Assert
        assert response.status_code == 422

    def test_invalid_phone_returns_422(self, client: TestClient):
        # Arrange
        sid = _create_session(client)

        # Act
        response = client.put(f"/api/sessions/{sid}/contact", json={"phone": "abc"})

        # Assert
        assert response.status_code == 422


class TestSessionLifecycle:
    def test_session_cap_reached_returns_422_too_many_sessions(self, client: TestClient):
        # Arrange: depozit cu plafon de o singura sesiune, injectat pe app.state
        client.app.state.sessions = SessionStore(max_sessions=1, ttl_minutes=30)
        first = client.post("/api/sessions")
        assert first.status_code == 200

        # Act
        response = client.post("/api/sessions")

        # Assert
        assert response.status_code == 422
        assert response.json()["code"] == "too_many_sessions"

    def test_session_expires_after_ttl_via_injected_clock_without_sleep(
        self, client: TestClient
    ):
        # Arrange: ceas controlat manual, nicio pauza reala
        current_time = {"value": datetime(2026, 1, 1, 12, 0, tzinfo=UTC)}
        client.app.state.sessions = SessionStore(
            ttl_minutes=30, now=lambda: current_time["value"]
        )
        sid = client.post("/api/sessions").json()["session_id"]

        # Act: timpul avanseaza peste TTL-ul de inactivitate, fara sleep
        current_time["value"] += timedelta(minutes=31)
        response = client.get(f"/api/sessions/{sid}")

        # Assert
        assert response.status_code == 404


class TestOrderViewMinimization:
    def test_kitchen_view_excludes_contact_and_address(self, client: TestClient):
        # Arrange
        sid = _ready_delivery_session(client)
        client.post(f"/api/sessions/{sid}/place", json={"idempotency_key": "kitchen-view-1"})

        # Act
        response = client.get("/api/orders", params={"view": "kitchen"})

        # Assert
        assert response.status_code == 200
        assert response.json()
        for order in response.json():
            assert "contact" not in order
            assert "address" not in order

    def test_driver_view_includes_address(self, client: TestClient):
        # Arrange: comanda de livrare adusa in starea READY (vizibila livratorului)
        sid = _ready_delivery_session(client)
        order_id = client.post(
            f"/api/sessions/{sid}/place", json={"idempotency_key": "driver-view-1"}
        ).json()["id"]
        client.post(f"/api/orders/{order_id}/status", json={"status": "in_kitchen"})
        client.post(f"/api/orders/{order_id}/status", json={"status": "ready"})

        # Act
        response = client.get("/api/orders", params={"view": "driver"})

        # Assert
        assert response.status_code == 200
        matching = next(order for order in response.json() if order["id"] == order_id)
        assert matching["address"] is not None

    def test_orders_without_view_returns_422(self, client: TestClient):
        # Act
        response = client.get("/api/orders")

        # Assert
        assert response.status_code == 422
        assert response.json()["code"] == "view_required"


class TestOrderIdGeneration:
    def test_place_order_retries_after_id_collision_instead_of_500(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        # Arrange: prima comanda ocupa id-ul "CMD-0001"
        first_sid = _ready_delivery_session(client)
        first_order_id = client.post(
            f"/api/sessions/{first_sid}/place", json={"idempotency_key": "collide-1"}
        ).json()["id"]

        real_next_order_id = routes_session_module.next_order_id
        calls = {"count": 0}

        def _colliding_next_order_id(db_session):
            calls["count"] += 1
            if calls["count"] == 1:
                # Forteaza o coliziune reala pe cheia primara `id` la primul commit.
                return first_order_id
            return real_next_order_id(db_session)

        monkeypatch.setattr(routes_session_module, "next_order_id", _colliding_next_order_id)

        # Act: a doua comanda ar coliza pe `id` la prima incercare
        second_sid = _ready_delivery_session(client)
        response = client.post(
            f"/api/sessions/{second_sid}/place", json={"idempotency_key": "collide-2"}
        )

        # Assert: reincercarea reuseste — niciun 500, id nou, distinct
        assert response.status_code == 200
        assert response.json()["id"] != first_order_id
        assert calls["count"] >= 2

    def test_order_id_is_not_recycled_after_row_deletion(self, client: TestClient):
        # Arrange: doua comenzi plasate, "CMD-0001" si "CMD-0002"
        first_sid = _ready_delivery_session(client)
        first_order_id = client.post(
            f"/api/sessions/{first_sid}/place", json={"idempotency_key": "recycle-1"}
        ).json()["id"]
        second_sid = _ready_delivery_session(client)
        second_order_id = client.post(
            f"/api/sessions/{second_sid}/place", json={"idempotency_key": "recycle-2"}
        ).json()["id"]

        # Act: sterge prima comanda direct din DB — un `COUNT(*)` ar recicla id-ul ei
        with Session(get_engine()) as db_session:
            row = db_session.get(OrderRow, first_order_id)
            db_session.delete(row)
            db_session.commit()

        third_sid = _ready_delivery_session(client)
        third_order_id = client.post(
            f"/api/sessions/{third_sid}/place", json={"idempotency_key": "recycle-3"}
        ).json()["id"]

        # Assert: id-ul nou nu se reciclista si nu coliziona cu cel ramas
        assert third_order_id not in (first_order_id, second_order_id)
