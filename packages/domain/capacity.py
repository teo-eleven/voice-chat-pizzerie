"""ETA calculat din capacitatea cuptorului, nu din numărul de comenzi.

Ridicarea și livrarea consumă aceeași coadă de bucătărie — diferă doar drumul de
livrare, adăugat la final.
"""

from __future__ import annotations

import math

from .enums import Fulfillment
from .models import CartLine, EtaWindow, KitchenConfig, ValidationIssue


def line_slot_minutes(line: CartLine) -> int:
    """Cât timp de cuptor consumă o linie de coș. 0 la produse fără cuptor."""
    return line.oven_slots * line.bake_minutes * line.qty


def order_slot_minutes(lines: tuple[CartLine, ...]) -> int:
    """Timpul total de cuptor cerut de o comandă."""
    return sum(line_slot_minutes(line) for line in lines)


def order_prep_minutes(lines: tuple[CartLine, ...]) -> int:
    """Maximul, nu suma: pregătirea în afara cuptorului se face în paralel cu coacerea."""
    if not lines:
        return 0
    return max(line.prep_minutes for line in lines)


def queue_slot_minutes(queue: tuple[tuple[CartLine, ...], ...]) -> int:
    """Munca de cuptor rămasă în coadă, înaintea comenzii noi."""
    return sum(order_slot_minutes(order_lines) for order_lines in queue)


def _round_up_to_multiple(minutes: int, step: int) -> int:
    """Rotunjește `minutes` în sus la cel mai apropiat multiplu de `step`."""
    return math.ceil(minutes / step) * step


def estimate_eta(
    new_lines: tuple[CartLine, ...],
    *,
    pending_slot_minutes: int,
    fulfillment: Fulfillment,
    config: KitchenConfig,
) -> EtaWindow:
    """Estimează intervalul de timp comunicabil clientului, rotunjit în sus."""
    oven_wall_clock = math.ceil(
        (pending_slot_minutes + order_slot_minutes(new_lines)) / config.oven_slots
    )
    kitchen = max(oven_wall_clock, order_prep_minutes(new_lines)) + config.order_overhead_minutes
    total = kitchen + config.safety_buffer_minutes
    if fulfillment == Fulfillment.DELIVERY:
        total += config.delivery_drive_minutes

    rounded = _round_up_to_multiple(total, config.quote_rounding_minutes)
    return EtaWindow(min_minutes=rounded, max_minutes=rounded + config.quote_window_minutes)


def can_promise(eta: EtaWindow, config: KitchenConfig) -> bool:
    """Sub pragul comercial peste care nu mai promitem, ca să nu mințim clientul."""
    return eta.min_minutes <= config.max_promisable_minutes


def overload_issue(eta: EtaWindow, config: KitchenConfig) -> ValidationIssue | None:
    """Motiv structurat de refuz când bucătăria e suprasolicitată. Spune adevărul."""
    if can_promise(eta, config):
        return None
    return ValidationIssue(
        code="kitchen_overloaded",
        message=(
            f"Momentan bucătăria este foarte încărcată și timpul real de așteptare "
            f"ar fi {eta.spoken()}, peste ce putem promite. "
            "Vă recomand să reveniți mai târziu sau să acceptați această întârziere."
        ),
        field="eta",
    )
