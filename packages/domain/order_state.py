"""Automatul de stări al comenzii.

Tranzițiile trec întotdeauna prin `transition`: nicio parte a sistemului nu setează
`status` direct pe un `Order`.
"""

from __future__ import annotations

from .enums import Fulfillment, OrderStatus
from .errors import DomainError
from .models import Order

ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.NEW: frozenset({OrderStatus.IN_KITCHEN, OrderStatus.CANCELLED}),
    OrderStatus.IN_KITCHEN: frozenset({OrderStatus.READY, OrderStatus.CANCELLED}),
    OrderStatus.READY: frozenset(
        {OrderStatus.ASSIGNED, OrderStatus.PICKED_UP, OrderStatus.CANCELLED}
    ),
    OrderStatus.ASSIGNED: frozenset({OrderStatus.OUT, OrderStatus.CANCELLED}),
    OrderStatus.OUT: frozenset({OrderStatus.DELIVERED}),
    OrderStatus.DELIVERED: frozenset(),
    OrderStatus.PICKED_UP: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}

#: Stări rezervate exclusiv livrării.
_DELIVERY_ONLY_STATUSES = frozenset({OrderStatus.ASSIGNED, OrderStatus.OUT, OrderStatus.DELIVERED})
#: Stări rezervate exclusiv ridicării.
_PICKUP_ONLY_STATUSES = frozenset({OrderStatus.PICKED_UP})

#: Stări vizibile bucătăriei: tot ce nu a ajuns încă la client.
_KITCHEN_VISIBLE_STATUSES = frozenset(
    {OrderStatus.NEW, OrderStatus.IN_KITCHEN, OrderStatus.READY}
)
#: Stări vizibile livratorului: doar de când comanda e gata, până când pornește la drum.
_DRIVER_VISIBLE_STATUSES = frozenset({OrderStatus.READY, OrderStatus.ASSIGNED, OrderStatus.OUT})


def _matches_fulfillment(new_status: OrderStatus, fulfillment: Fulfillment) -> bool:
    """Statusul cerut e coerent cu tipul de livrare al comenzii."""
    if new_status in _DELIVERY_ONLY_STATUSES:
        return fulfillment == Fulfillment.DELIVERY
    if new_status in _PICKUP_ONLY_STATUSES:
        return fulfillment == Fulfillment.PICKUP
    return True


def can_transition(order: Order, new_status: OrderStatus) -> bool:
    """Tranziția e permisă de tabel și coerentă cu `fulfillment`."""
    allowed = new_status in ALLOWED_TRANSITIONS.get(order.status, frozenset())
    return allowed and _matches_fulfillment(new_status, order.fulfillment)


def transition(order: Order, new_status: OrderStatus) -> Order:
    """Întoarce un `Order` nou cu statusul actualizat. Ridică `DomainError` la refuz."""
    if new_status not in ALLOWED_TRANSITIONS.get(order.status, frozenset()):
        raise DomainError.of(
            "invalid_transition",
            f"Comanda nu poate trece din starea {order.status.value} în {new_status.value}.",
            field="status",
        )
    if not _matches_fulfillment(new_status, order.fulfillment):
        raise DomainError.of(
            "fulfillment_mismatch",
            f"Starea {new_status.value} nu este compatibilă cu tipul de preluare al comenzii.",
            field="status",
        )
    return order.model_copy(update={"status": new_status})


def is_visible_to_kitchen(order: Order) -> bool:
    """Bucătăria vede toate comenzile, livrare și ridicare, până la predare."""
    return order.status in _KITCHEN_VISIBLE_STATUSES


def is_visible_to_driver(order: Order) -> bool:
    """Doar livrări, doar din `READY` încolo. Livratorul nu vede ridicările."""
    return order.needs_driver and order.status in _DRIVER_VISIBLE_STATUSES


def is_final(status: OrderStatus) -> bool:
    """O stare fără ieșiri în tabelul de tranziții e finală."""
    return not ALLOWED_TRANSITIONS.get(status, frozenset())
