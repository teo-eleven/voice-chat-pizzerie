"""Tabelul de persistenta pentru comenzi, separat de modelele de domeniu.

`Order` (domeniu) e imutabil si nu are treaba cu SQL. `OrderRow` e randul din
baza: cosul, adresa si contactul se serializeaza ca JSON intr-o coloana text,
restul campurilor interogate direct de dashboard-uri (bucatarie/livrator) au
coloane native.
"""

from __future__ import annotations

import threading
from datetime import datetime

from sqlmodel import Field, Session, SQLModel, select

from packages.domain.enums import Fulfillment, OrderStatus, PaymentMethod
from packages.domain.models import Address, Cart, Contact, EtaWindow, Order

#: Serializeaza generarea id-ului de comanda intre thread-uri: doua plasari
#: concurente nu trebuie sa citeasca acelasi maxim si sa calculeze acelasi id
#: inainte ca vreuna dintre ele sa fi facut commit.
ORDER_ID_LOCK = threading.Lock()


class OrderRow(SQLModel, table=True):
    """Randul de comanda persistat. `to_domain`/`from_domain` fac conversia."""

    id: str = Field(primary_key=True)
    status: OrderStatus
    fulfillment: Fulfillment
    payment: PaymentMethod
    idempotency_key: str = Field(unique=True, index=True)
    created_at: datetime
    eta_min: int
    eta_max: int
    allergy_note: str | None = None
    #: JSON serializat: coșul complet, cu liniile lui.
    cart_json: str
    #: JSON serializat, absent la ridicare.
    address_json: str | None = None
    contact_json: str


def from_domain(order: Order) -> OrderRow:
    """`Order` de domeniu -> rand persistabil. Comanda plasata are deja ETA si created_at."""
    assert order.eta is not None, "o comanda plasata trebuie sa aiba ETA calculat"
    assert order.created_at is not None, "o comanda plasata trebuie sa aiba created_at"
    return OrderRow(
        id=order.id,
        status=order.status,
        fulfillment=order.fulfillment,
        payment=order.payment,
        idempotency_key=order.idempotency_key,
        created_at=order.created_at,
        eta_min=order.eta.min_minutes,
        eta_max=order.eta.max_minutes,
        allergy_note=order.allergy_note,
        cart_json=order.cart.model_dump_json(),
        address_json=order.address.model_dump_json() if order.address is not None else None,
        contact_json=order.contact.model_dump_json(),
    )


def to_domain(row: OrderRow) -> Order:
    """Rand persistat -> `Order` de domeniu, cu toate obiectele reconstruite din JSON."""
    address = Address.model_validate_json(row.address_json) if row.address_json else None
    return Order(
        id=row.id,
        cart=Cart.model_validate_json(row.cart_json),
        fulfillment=row.fulfillment,
        contact=Contact.model_validate_json(row.contact_json),
        payment=row.payment,
        status=row.status,
        address=address,
        eta=EtaWindow(min_minutes=row.eta_min, max_minutes=row.eta_max),
        allergy_note=row.allergy_note,
        created_at=row.created_at,
        idempotency_key=row.idempotency_key,
    )


def next_order_id(session: Session) -> str:
    """ID determinist `CMD-0001`, `CMD-0002`, ..., derivat din maximul sufixului
    numeric existent — nu dintr-un `COUNT(*)`.

    Un `COUNT(*)` e gresit si fara concurenta: dupa o stergere de rand, id-ul
    "liber" calculat din numarul de randuri se reciclista si coliziona cu un
    id inca existent. Apelantul trebuie sa tina `ORDER_ID_LOCK` cat cheama
    aceasta functie si face insert-ul, ca doua plasari concurente sa nu
    calculeze acelasi id inainte de commit.
    """
    ids = session.exec(select(OrderRow.id)).all()
    max_suffix = max((_numeric_suffix(order_id) for order_id in ids), default=0)
    return f"CMD-{max_suffix + 1:04d}"


def _numeric_suffix(order_id: str) -> int:
    suffix = order_id.removeprefix("CMD-")
    return int(suffix) if suffix.isdigit() else 0
