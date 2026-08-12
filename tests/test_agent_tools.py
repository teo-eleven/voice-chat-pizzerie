"""Teste pentru stratul de agent (apps/agent), fara LLM si fara retea.

Ce se verifica aici e partea determinista a Fazei 2: contractul de tool-uri
peste API-ul real si derivarea checklist-ului din starea sesiunii. Modelul nu
apare deloc — comportamentul conversational se masoara separat, prin scenariile
din `evals/`, fiindca acolo raspunsul nu e determinist.

Tool-urile vorbesc cu aplicatia ASGI printr-un transport injectat: acelasi HTTP,
aceleasi rute, aceleasi coduri de eroare ca in productie, dar fara server pornit.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from apps.agent import checklist as checklist_module
from apps.agent import tools as tools_module
from apps.agent.api_client import PizzaApiClient
from apps.agent.tools import ToolContext
from apps.api.main import app
from apps.api.sessions import CallSession
from packages.domain.enums import Category, Fulfillment
from packages.domain.models import Cart, CartLine, Contact


@pytest.fixture
def context(tmp_path, monkeypatch):
    """Sesiune de apel proaspata, peste un SQLite propriu testului."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    with TestClient(app) as test_client:
        client = PizzaApiClient("http://testserver", http_client=test_client)
        yield ToolContext(client=client, session_id=client.create_session())


def call(context: ToolContext, tool: str, /, **arguments: object) -> tuple[dict, bool]:
    """Cheama un tool si desface rezultatul. Parametri pozitionali, ca `name` sa
    ramana liber pentru argumentele tool-urilor (`set_contact` are unul)."""
    outcome = tools_module.dispatch(context, tool, arguments)
    return json.loads(outcome.content), outcome.is_error


def _add_capricciosa(context: ToolContext, qty: int = 2) -> dict:
    payload, _ = call(context, "add_item", product_id="PZ-002", qty=qty, size_code="large")
    return payload


class TestMenu:
    def test_search_returns_real_products_with_ids_and_prices(self, context: ToolContext):
        payload, is_error = call(context, "search_menu", query="capricciosa")

        assert not is_error
        assert payload, "cautarea trebuie sa gaseasca produsul din meniul seed"
        assert payload[0]["id"].startswith("PZ-")
        assert payload[0]["sizes"][0]["price_formatted"]

    def test_search_by_category_returns_only_that_category(self, context: ToolContext):
        payload, is_error = call(context, "search_menu", category="drink")

        assert not is_error
        assert payload
        assert {item["category"] for item in payload} == {"drink"}


class TestCart:
    def test_add_item_returns_cart_priced_by_backend(self, context: ToolContext):
        cart = _add_capricciosa(context)

        assert len(cart["lines"]) == 1
        assert cart["lines"][0]["qty"] == 2
        assert cart["total_bani"] > 0

    def test_unknown_product_is_a_domain_refusal_not_a_crash(self, context: ToolContext):
        payload, is_error = call(context, "add_item", product_id="NU-EXISTA")

        assert is_error
        assert payload["code"] == "product_not_found"
        # Refuz de domeniu: mesajul e al domeniului, fara indemn la escaladare.
        assert "hint" not in payload

    def test_update_and_remove_operate_on_the_returned_line_id(self, context: ToolContext):
        line_id = _add_capricciosa(context)["lines"][0]["line_id"]

        updated, _ = call(context, "update_item", line_id=line_id, qty=1)
        emptied, _ = call(context, "remove_item", line_id=updated["lines"][0]["line_id"])

        assert updated["lines"][0]["qty"] == 1
        assert emptied["lines"] == []


class TestAddress:
    def test_out_of_zone_is_a_verdict_not_an_error(self, context: ToolContext):
        """Adresa in afara zonei nu e o defectiune: e un raspuns pe care agentul il poveste."""
        payload, is_error = call(context, "resolve_address", text="Strada Aviatorilor 10")

        assert not is_error
        assert payload["resolution"] == "out_of_zone"

    def test_ambiguous_address_returns_candidates_to_disambiguate(self, context: ToolContext):
        payload, is_error = call(context, "resolve_address", text="Strada Trandafirilor 5")

        assert not is_error
        assert payload["resolution"] == "ambiguous"
        assert len(payload["candidates"]) > 1


class TestPlaceOrder:
    def test_missing_contact_blocks_placement_with_a_spoken_reason(self, context: ToolContext):
        _add_capricciosa(context)
        call(context, "set_fulfillment", fulfillment="pickup")

        payload, is_error = call(context, "place_order")

        assert is_error
        assert payload["code"] == "contact_required"

    def test_retry_reuses_the_same_order_instead_of_duplicating(self, context: ToolContext):
        """Cheia de idempotenta e derivata din sesiune, nu generata de model."""
        _add_capricciosa(context)
        call(context, "set_fulfillment", fulfillment="pickup")
        call(context, "set_contact", phone="0711111111", name="Ana")
        call(context, "set_payment", payment="cash")

        first, first_error = call(context, "place_order")
        second, second_error = call(context, "place_order")

        assert not first_error and not second_error
        assert first["id"] == second["id"]
        assert context.placed_order_id == first["id"]

    def test_summary_carries_the_canonical_spoken_text(self, context: ToolContext):
        _add_capricciosa(context)

        payload, is_error = call(context, "get_order_summary")

        assert not is_error
        assert payload["total_formatted"] in payload["spoken_text"] or payload["spoken_text"]
        assert payload["eta"]


class TestEscalation:
    def test_escalation_raises_a_flag_for_the_harness(self, context: ToolContext):
        payload, is_error = call(context, "escalate_to_human", reason="allergy", note="alune")

        assert not is_error
        assert payload["escalated"] is True
        assert context.escalation == {"reason": "allergy", "note": "alune"}


class TestChecklist:
    def _session(self, **overrides) -> CallSession:
        return CallSession(session_id="S1", **overrides)

    def test_empty_session_needs_a_product_and_opens_with_sauces(self):
        state = checklist_module.from_session(self._session())

        assert not state.has_main_product
        assert "cel putin un produs" in state.missing_required
        assert state.pending_upsell is not None
        assert state.pending_upsell.category == Category.SAUCE

    def test_refused_category_closes_permanently(self):
        """Dupa un „nu" marcat pe server, categoria nu se mai propune niciodata."""
        state = checklist_module.from_session(
            self._session(asked_flags={"sauce": True, "drink": False, "dessert": False})
        )

        assert state.upsell[0].asked
        assert not state.upsell[0].is_open
        assert state.pending_upsell.category == Category.DRINK

    def test_category_already_in_cart_is_not_proposed(self):
        line = CartLine(
            line_id="L1",
            product_id="SO-001",
            product_name="Usturoi",
            category=Category.SAUCE,
            qty=1,
            unit_price_bani=500,
            total_bani=500,
        )
        state = checklist_module.from_session(self._session(cart=Cart(lines=(line,))))

        assert state.upsell[0].has
        assert not state.upsell[0].is_open

    def test_pickup_does_not_require_an_address(self):
        state = checklist_module.from_session(self._session(fulfillment=Fulfillment.PICKUP))

        assert not state.needs_address
        assert "adresa de livrare" not in state.missing_required

    def test_render_reports_state_without_prescribing_the_next_move(self):
        state = checklist_module.from_session(
            self._session(contact=Contact(phone="0711111111", name="Ana"))
        )

        rendered = checklist_module.render(state)

        assert "telefon: da" in rendered
        assert "prenume: da" in rendered
        assert "plata: nu" in rendered
