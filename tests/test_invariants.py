"""Invarianții interni nu se mai bazează pe `assert`.

Un `assert` dispare complet când interpretorul rulează cu `python -O`. Acolo unde
verifica o precondiție reală — o comandă fără ETA, un produs fără preț — dispariția
lui nu ar produce o eroare clară, ci un `AttributeError` sau un `None` strecurat mai
departe în date deja considerate validate.

Toate se ridică drept `ValueError`, nu `DomainError`: `errors.py` spune explicit că
`DomainError` e refuz motivat al domeniului, „niciodată folosit pentru bug-uri de
programare", iar el se traduce în 422 către client. Un catalog prost configurat sau o
sesiune incompletă ajunsă până la construirea comenzii nu sunt refuzuri pe care
clientul le-ar putea corecta — sunt defecte ale noastre, și trebuie să arate ca atare.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.api.placement import _build_order
from apps.api.sessions import CallSession
from apps.api.tables import from_domain
from packages.domain import cart_ops
from packages.domain.enums import Category, Fulfillment, OrderStatus, PaymentMethod
from packages.domain.models import (
    Cart,
    Contact,
    EtaWindow,
    LineSpec,
    Order,
    PricingContext,
    Product,
    ZoneConfig,
)

_ZONE = ZoneConfig(
    polygon=((44.0, 26.0), (44.0, 26.5), (44.5, 26.5), (44.5, 26.0)),
    min_order_bani=0,
    delivery_fee_bani=0,
)
_CONTEXT = PricingContext(fulfillment=Fulfillment.PICKUP, zone=_ZONE)


def _order(**overrides) -> Order:
    base = {
        "id": "CMD-0001",
        "cart": Cart(),
        "fulfillment": Fulfillment.PICKUP,
        "contact": Contact(phone="0711111111"),
        "payment": PaymentMethod.CASH,
        "status": OrderStatus.NEW,
        "eta": EtaWindow(min_minutes=20, max_minutes=30),
        "created_at": datetime.now(UTC),
        "idempotency_key": "key-1",
    }
    return Order(**{**base, **overrides})


class TestFromDomainRequiresPlacedOrderFields:
    def test_order_without_eta_raises_value_error(self):
        # Arrange
        order = _order(eta=None)

        # Act / Assert
        with pytest.raises(ValueError, match="ETA"):
            from_domain(order)

    def test_order_without_created_at_raises_value_error(self):
        # Arrange
        order = _order(created_at=None)

        # Act / Assert
        with pytest.raises(ValueError, match="created_at"):
            from_domain(order)


class TestMisconfiguredProductIsRefused:
    def test_product_without_sizes_and_without_price_raises_value_error(self):
        # Arrange: produs care a scapat de validarea catalogului — fara marimi si fara pret
        product = Product(id="BT-999", name="Suc fantoma", category=Category.DRINK)

        # Act / Assert
        with pytest.raises(ValueError, match="price_bani"):
            cart_ops.add_line(Cart(), product, spec=LineSpec(qty=1), context=_CONTEXT)


class TestBuildOrderRequiresCompleteSession:
    def test_session_without_contact_raises_value_error(self):
        # Arrange
        call_session = CallSession(session_id="S1", payment=PaymentMethod.CASH)

        # Act / Assert
        with pytest.raises(ValueError, match="contact"):
            _build_order(
                "CMD-0001",
                call_session,
                EtaWindow(min_minutes=20, max_minutes=30),
                "key-1",
                datetime.now(UTC),
                1,
            )

    def test_session_without_payment_raises_value_error(self):
        # Arrange
        call_session = CallSession(session_id="S1", contact=Contact(phone="0711111111"))

        # Act / Assert
        with pytest.raises(ValueError, match="plata"):
            _build_order(
                "CMD-0001",
                call_session,
                EtaWindow(min_minutes=20, max_minutes=30),
                "key-1",
                datetime.now(UTC),
                1,
            )
