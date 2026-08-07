"""Sursa unică de adevăr pentru totalurile coșului. Fără I/O, fără stare."""

from __future__ import annotations

from . import money
from .enums import Fulfillment
from .models import Cart, CartLine, ValidationIssue, ZoneConfig


def line_total(unit_price_bani: int, qty: int) -> int:
    return unit_price_bani * qty


def delivery_fee(items_bani: int, fulfillment: Fulfillment, zone: ZoneConfig) -> int:
    if fulfillment == Fulfillment.PICKUP:
        return 0
    # Un coș gol nu are ce livra: fără asta, ștergerea ultimei linii lăsa un total nenul.
    if items_bani <= 0:
        return 0
    threshold = zone.free_delivery_threshold_bani
    if threshold is not None and items_bani >= threshold:
        return 0
    return zone.delivery_fee_bani


def price_cart(lines: tuple[CartLine, ...], fulfillment: Fulfillment, zone: ZoneConfig) -> Cart:
    """Recalculează `items_bani`, taxa de livrare și `total_bani` din liniile date."""
    items_bani = money.sum_bani(line.total_bani for line in lines)
    fee_bani = delivery_fee(items_bani, fulfillment, zone)
    return Cart(
        lines=lines,
        items_bani=items_bani,
        delivery_fee_bani=fee_bani,
        total_bani=items_bani + fee_bani,
    )


def check_minimum(
    cart: Cart, fulfillment: Fulfillment, zone: ZoneConfig
) -> ValidationIssue | None:
    """Comanda minimă se aplică doar la livrare, pe `items_bani` (fără taxa de livrare)."""
    if fulfillment != Fulfillment.DELIVERY:
        return None
    missing_bani = zone.min_order_bani - cart.items_bani
    if missing_bani <= 0:
        return None
    return ValidationIssue(
        code="below_minimum_order",
        message=(
            f"Comanda minimă pentru livrare este {money.format_ron(zone.min_order_bani)}. "
            f"Mai este nevoie de {money.format_ron(missing_bani)}."
        ),
        field="items_bani",
    )
