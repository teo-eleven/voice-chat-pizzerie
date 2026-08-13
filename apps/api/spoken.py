"""Textul canonic rostit clientului: rezumatul comenzii.

E singurul loc unde se compune ce aude clientul despre comanda lui. Agentul din
Faza 2 il primeste gata facut prin `get_order_summary` si il citeste ca atare — nu
si-l reformuleaza si nu recalculeaza nimic din el.
"""

from __future__ import annotations

from packages.domain import money
from packages.domain.enums import Fulfillment
from packages.domain.models import CartLine, EtaWindow

from .sessions import CallSession


def summary_text(call_session: CallSession, eta: EtaWindow) -> str:
    lines = call_session.cart.lines
    items_text = "; ".join(_line_spoken(line) for line in lines) if lines else "niciun produs"
    total_text = money.spoken_ron(call_session.cart.total_bani)
    return (
        f"Ați ales: {items_text}. Total: {total_text}. "
        f"{_fulfillment_spoken(call_session)} Timp estimat: {eta.spoken()}."
    )


def _line_spoken(line: CartLine) -> str:
    size = f" {line.size_label}" if line.size_label else ""
    removed = f", fără {', '.join(line.removed_ingredients)}" if line.removed_ingredients else ""
    return f"{line.qty} x {line.product_name}{size}{removed}"


def _fulfillment_spoken(call_session: CallSession) -> str:
    if call_session.fulfillment == Fulfillment.PICKUP:
        return "Ridicare de la restaurant."
    if call_session.address is None:
        return "Livrare la adresă nespecificată."
    return f"Livrare la {call_session.address.formatted}."
