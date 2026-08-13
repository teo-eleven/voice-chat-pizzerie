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

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlmodel import Session, col, select

from packages.domain import order_state
from packages.domain.enums import OrderStatus
from packages.domain.errors import DomainError
from packages.domain.models import Order

from .db import SessionDep
from .events import EventHub
from .schemas import KitchenOrderOut, SetOrderStatusRequest
from .tables import OrderRow, to_domain

router = APIRouter(prefix="/api", tags=["orders"])

#: Valorile acceptate de `view`, ca tip: FastAPI le valideaza singur si le pune in
#: OpenAPI, in loc sa le verificam manual dintr-un `str` liber.
View = Literal["kitchen", "driver"]

_VIEW_FILTERS: dict[View, Callable[[Order], bool]] = {
    "kitchen": order_state.is_visible_to_kitchen,
    "driver": order_state.is_visible_to_driver,
}


@router.get("/orders")
def list_orders(
    db_session: SessionDep, view: View | None = None
) -> list[Order] | list[KitchenOrderOut]:
    """`view` lipsa si `view` invalid sunt doua lucruri diferite.

    Lipsa ajunge aici si primeste `view_required` — refuzul deliberat de a scoate tot
    ce e in baza catre orice client care cheama ruta fara parametru. O valoare
    prezenta dar necunoscuta nici nu ajunge in functie: `Literal` o opreste in
    validarea FastAPI, cu un mesaj care spune exact ce valori sunt acceptate.
    """
    if view is None:
        raise DomainError.of(
            "view_required",
            "Parametrul „view” este obligatoriu si trebuie sa fie „kitchen” sau „driver”.",
            field="view",
        )
    visible = _VIEW_FILTERS[view]
    rows = db_session.exec(select(OrderRow).order_by(col(OrderRow.created_at))).all()
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
    """Muta comanda in statusul cerut si anunta dashboard-urile.

    Ruta e `async` doar pentru `hub.broadcast`; citirea randului si `commit()` sunt
    blocante si ar tine event loop-ul ocupat, asa ca merg prin `run_in_threadpool`.
    Vezi nota lunga din `routes_session.place_order`.
    """
    updated = await run_in_threadpool(_apply_status, order_id, body.status, db_session)

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


def _apply_status(order_id: str, status: OrderStatus, db_session: Session) -> Order:
    """Partea sincrona a tranzitiei, rulata in thread-pool.

    `OrderRow` e singurul loc din aplicatie unde se muteaza ceva in loc: e randul
    urmarit de unit-of-work-ul SQLAlchemy, deci persistenta e granita deliberata a
    imutabilitatii. Statusul nou vine tot din `order_state.transition`, care valideaza
    tranzitia si intoarce un `Order` nou — randul doar il preia.
    """
    row = _require_order_row(order_id, db_session)
    updated = order_state.transition(to_domain(row), status)
    row.status = updated.status
    db_session.add(row)
    db_session.commit()
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
