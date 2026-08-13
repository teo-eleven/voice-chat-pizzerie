"""Plafoanele de lungime traiesc in domeniu, nu doar in DTO-urile HTTP.

`apps/api/schemas.py` plafoneaza deja `phone`, `name` si `idempotency_key` la
granita HTTP, dar agentul din Faza 2 construieste `Contact` / `Address` / `Order`
direct, prin `apps/agent`, fara sa treaca prin schemele de cerere. Daca limita ar
trai doar in DTO, pe acea cale ar disparea tacut. Testele de aici fixeaza
invariantul acolo unde nu poate fi ocolit: pe modelul insusi.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from packages.domain.enums import AddressResolution, Category, Fulfillment, PaymentMethod
from packages.domain.limits import (
    MAX_ALLERGY_NOTE_LEN,
    MAX_IDEMPOTENCY_KEY_LEN,
    MAX_NAME_LEN,
    MAX_NOTES_LEN,
    MAX_PHONE_LEN,
)
from packages.domain.models import (
    Address,
    AddressCandidate,
    AddressResult,
    Cart,
    CartLine,
    Contact,
    Order,
)


def _drink_line() -> CartLine:
    return CartLine(
        line_id="L1",
        product_id="BT-006",
        product_name="Apa plata",
        category=Category.DRINK,
        qty=1,
        unit_price_bani=500,
        total_bani=500,
    )


def _order(**overrides) -> Order:
    base = {
        "id": "CMD-0001",
        "cart": Cart(),
        "fulfillment": Fulfillment.PICKUP,
        "contact": Contact(phone="0711111111"),
        "payment": PaymentMethod.CASH,
    }
    return Order(**{**base, **overrides})


class TestCheclistHelpers:
    """`Cart.has_category` și `AddressResult.is_usable` există pentru checklist-ul din
    Faza 2 (`apps/agent/checklist.py`): „nu întrebăm de băuturi dacă are deja băuturi",
    respectiv „adresa e utilizabilă doar dacă e rezolvată și fără ambiguitate".

    Erau singurele două linii neacoperite din `models.py` și, nefiind chemate de nimeni
    încă, arătau ca dead code. Sunt fixate aici ca să nu fie șterse din greșeală înainte
    ca faza care le folosește să înceapă.
    """

    def test_has_category_is_true_when_a_line_matches(self):
        # Arrange
        cart = Cart(lines=(_drink_line(),))

        # Act / Assert
        assert cart.has_category(Category.DRINK) is True
        assert cart.has_category(Category.DESSERT) is False

    def test_is_usable_only_for_a_single_resolved_candidate(self):
        # Arrange
        candidate = AddressCandidate(
            address=Address(street="Strada Stefan cel Mare", number="24"),
            confidence=0.9,
            in_zone=True,
        )
        resolved = AddressResult(resolution=AddressResolution.OK, candidates=(candidate,))
        ambiguous = AddressResult(
            resolution=AddressResolution.OK, candidates=(candidate, candidate)
        )
        out_of_zone = AddressResult(resolution=AddressResolution.OUT_OF_ZONE)

        # Act / Assert
        assert resolved.is_usable is True
        assert ambiguous.is_usable is False
        assert out_of_zone.is_usable is False


class TestContactLimits:
    def test_accepts_name_at_the_limit(self):
        # Arrange
        name = "a" * MAX_NAME_LEN

        # Act
        contact = Contact(phone="0711111111", name=name)

        # Assert
        assert contact.name == name

    def test_rejects_name_over_the_limit(self):
        # Arrange
        name = "a" * (MAX_NAME_LEN + 1)

        # Act / Assert
        with pytest.raises(ValidationError):
            Contact(phone="0711111111", name=name)

    def test_rejects_phone_over_the_limit(self):
        # Arrange
        phone = "0" * (MAX_PHONE_LEN + 1)

        # Act / Assert
        with pytest.raises(ValidationError):
            Contact(phone=phone)


class TestAddressLimits:
    def test_accepts_notes_at_the_limit(self):
        # Arrange
        notes = "n" * MAX_NOTES_LEN

        # Act
        address = Address(street="Strada Stefan cel Mare", number="24", notes=notes)

        # Assert
        assert address.notes == notes

    def test_rejects_notes_over_the_limit(self):
        # Arrange
        notes = "n" * (MAX_NOTES_LEN + 1)

        # Act / Assert
        with pytest.raises(ValidationError):
            Address(street="Strada Stefan cel Mare", number="24", notes=notes)


class TestOrderLimits:
    def test_accepts_allergy_note_at_the_limit(self):
        # Arrange
        note = "x" * MAX_ALLERGY_NOTE_LEN

        # Act
        order = _order(allergy_note=note)

        # Assert
        assert order.allergy_note == note

    def test_rejects_allergy_note_over_the_limit(self):
        # Arrange
        note = "x" * (MAX_ALLERGY_NOTE_LEN + 1)

        # Act / Assert
        with pytest.raises(ValidationError):
            _order(allergy_note=note)

    def test_rejects_idempotency_key_over_the_limit(self):
        # Arrange
        key = "k" * (MAX_IDEMPOTENCY_KEY_LEN + 1)

        # Act / Assert
        with pytest.raises(ValidationError):
            _order(idempotency_key=key)
