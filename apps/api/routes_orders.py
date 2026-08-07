"""Comenzi si dashboard-uri: bucatarie, livrator, tranzitii de status.

`view=kitchen` intoarce un model minimal (`KitchenOrderOut`), fara `contact` si
fara `address`: ecranul de bucatarie are nevoie de continut, alergii si
status, nu de telefonul clientului sau adresa completa cu interfon.
`view=driver` intoarce `Order` intreg — livratorul are nevoie operational de
adresa si telefon. `view` e obligatoriu: fara el, ruta refuza cu 422, nu mai
scoate implicit tot ce e in baza de date pe orice client care o cheama fara
parametru.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import Session, select

from packages.domain import order_state
from packages.domain.errors import DomainError
from packages.domain.models import Order

from .db import SessionDep
from .events import EventHub
from .schemas import KitchenOrderOut, SetOrderStatusRequest
from .tables import OrderRow, to_domain

router = APIRouter(prefix="/api", tags=["orders"])

_VIEW_FILTERS = {
    "kitchen": order_state.is_visible_to_kitchen,
    "driver": order_state.is_visible_to_driver,
}


@router.get("/orders")
def list_orders(
    db_session: SessionDep, view: str | None = None
) -> list[Order] | list[KitchenOrderOut]:
    visible = _VIEW_FILTERS.get(view) if view is not None else None
    if visible is None:
        raise DomainError.of(
            "view_required",
            "Parametrul „view” este obligatoriu si trebuie sa fie „kitchen” sau „driver”.",
            field="view",
        )
    rows = db_session.exec(select(OrderRow).order_by(OrderRow.created_at)).all()
    orders = [order for order in (to_domain(row) for row in rows) if visible(order)]
    if view == "kitchen":
        return [_to_kitchen_view(order) for order in orders]
    return orders


@router.get("/orders/{order_id}", response_model=Order)
def get_order(order_id: str, db_session: SessionDep) -> Order:
    return _require_order_row_as_domain(order_id, db_session)


@router.post("/orders/{order_id}/status", response_model=Order)
async def set_order_status(
    order_id: str, body: SetOrderStatusRequest, request: Request, db_session: SessionDep
) -> Order:
    row = _require_order_row(order_id, db_session)
    updated = order_state.transition(to_domain(row), body.status)
    row.status = updated.status
    db_session.add(row)
    db_session.commit()

    hub: EventHub = request.app.state.events
    await hub.broadcast(
        {
            "type": "order_status_changed",
            "order_id": updated.id,
            "status": updated.status.value,
            "fulfillment": updated.fulfillment.value,
        }
    )
    return updated


def _to_kitchen_view(order: Order) -> KitchenOrderOut:
    return KitchenOrderOut(
        id=order.id,
        status=order.status,
        fulfillment=order.fulfillment,
        cart=order.cart,
        eta=order.eta,
        allergy_note=order.allergy_note,
        created_at=order.created_at,
    )


def _require_order_row(order_id: str, db_session: Session) -> OrderRow:
    row = db_session.get(OrderRow, order_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Comanda „{order_id}” nu exista.")
    return row


def _require_order_row_as_domain(order_id: str, db_session: Session) -> Order:
    return to_domain(_require_order_row(order_id, db_session))
