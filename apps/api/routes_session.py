"""Rute care oglindesc 1:1 tool-urile agentului vocal (Faza 2) pe sesiunea de apel.

Fisierul tine doar orchestrarea HTTP. Regulile de plasare (re-validare, ETA, id,
persistenta) stau in `placement.py`, textul rostit in `spoken.py`, iar accesul la
sesiune in `session_access.py` — impartit cu `placement`, ca sa nu se inchida un
ciclu de import intre ele.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

from packages.domain import cart_ops, delivery_zone, money, pricing
from packages.domain.catalog import Catalog
from packages.domain.enums import UPSELL_ORDER, AddressResolution, Category
from packages.domain.errors import DomainError
from packages.domain.models import (
    AddressResult,
    Cart,
    Contact,
    LineChanges,
    LineSpec,
    Order,
    PricingContext,
    ZoneConfig,
)

from . import geocoding, placement, spoken
from .db import SessionDep
from .events import EventHub
from .schemas import (
    AddItemRequest,
    OrderSummaryOut,
    PlaceOrderRequest,
    ResolveAddressRequest,
    SetContactRequest,
    SetFulfillmentRequest,
    SetPaymentRequest,
    UpdateItemRequest,
)
from .session_access import mutate, require_call_session
from .sessions import CallSession, SessionStore

router = APIRouter(prefix="/api", tags=["sessions"])


@router.post("/sessions")
def create_session(request: Request) -> dict[str, str]:
    store: SessionStore = request.app.state.sessions
    return {"session_id": store.create().session_id}


@router.get("/sessions/{sid}", response_model=CallSession)
def get_session_state(sid: str, request: Request) -> CallSession:
    return require_call_session(request, sid)


@router.post("/sessions/{sid}/items", response_model=Cart)
def add_item(sid: str, body: AddItemRequest, request: Request) -> Cart:
    catalog: Catalog = request.app.state.catalog
    zone: ZoneConfig = request.app.state.zone
    product = catalog.by_id(body.product_id)
    if product is None:
        raise DomainError.of(
            "product_not_found", f"Produsul „{body.product_id}” nu exista.", field="product_id"
        )

    def apply(call_session: CallSession) -> CallSession:
        cart = cart_ops.add_line(
            call_session.cart,
            product,
            spec=LineSpec(
                qty=body.qty,
                size_code=body.size_code,
                removed_ingredients=body.removed_ingredients,
            ),
            context=PricingContext(fulfillment=call_session.fulfillment, zone=zone),
        )
        return call_session.model_copy(update={"cart": cart})

    return mutate(request, sid, apply).cart


@router.patch("/sessions/{sid}/items/{line_id}", response_model=Cart)
def update_item(sid: str, line_id: str, body: UpdateItemRequest, request: Request) -> Cart:
    catalog: Catalog = request.app.state.catalog
    zone: ZoneConfig = request.app.state.zone

    def apply(call_session: CallSession) -> CallSession:
        existing = next(
            (line for line in call_session.cart.lines if line.line_id == line_id), None
        )
        if existing is None:
            raise DomainError.of(
                "line_not_found", f"Linia „{line_id}” nu exista in cos.", field="line_id"
            )
        product = catalog.by_id(existing.product_id)
        if product is None:
            raise DomainError.of(
                "product_not_found",
                f"Produsul „{existing.product_id}” nu mai exista.",
                field="product_id",
            )
        cart = cart_ops.update_line(
            call_session.cart,
            line_id,
            product,
            changes=LineChanges(
                qty=body.qty,
                size_code=body.size_code,
                removed_ingredients=body.removed_ingredients,
            ),
            context=PricingContext(fulfillment=call_session.fulfillment, zone=zone),
        )
        return call_session.model_copy(update={"cart": cart})

    return mutate(request, sid, apply).cart


@router.delete("/sessions/{sid}/items/{line_id}", response_model=Cart)
def remove_item(sid: str, line_id: str, request: Request) -> Cart:
    zone: ZoneConfig = request.app.state.zone

    def apply(call_session: CallSession) -> CallSession:
        cart = cart_ops.remove_line(
            call_session.cart,
            line_id,
            context=PricingContext(fulfillment=call_session.fulfillment, zone=zone),
        )
        return call_session.model_copy(update={"cart": cart})

    return mutate(request, sid, apply).cart


@router.put("/sessions/{sid}/fulfillment", response_model=Cart)
def set_fulfillment(sid: str, body: SetFulfillmentRequest, request: Request) -> Cart:
    zone: ZoneConfig = request.app.state.zone

    def apply(call_session: CallSession) -> CallSession:
        cart = pricing.price_cart(call_session.cart.lines, body.fulfillment, zone)
        return call_session.model_copy(update={"fulfillment": body.fulfillment, "cart": cart})

    return mutate(request, sid, apply).cart


@router.post("/sessions/{sid}/address", response_model=AddressResult)
def resolve_address(sid: str, body: ResolveAddressRequest, request: Request) -> AddressResult:
    # Confirma ca sesiunea exista (si ii marcheaza accesul) inainte de geocodare.
    require_call_session(request, sid)
    candidates = geocoding.geocode(body.text, request.app.state.addresses)
    result = delivery_zone.resolve_from_candidates(candidates, request.app.state.zone)
    if result.resolution == AddressResolution.OK:
        address = result.candidates[0].address
        mutate(request, sid, lambda cs: cs.model_copy(update={"address": address}))
    return result


@router.put("/sessions/{sid}/contact", response_model=CallSession)
def set_contact(sid: str, body: SetContactRequest, request: Request) -> CallSession:
    contact = Contact(phone=body.phone, name=body.name)
    return mutate(request, sid, lambda cs: cs.model_copy(update={"contact": contact}))


@router.put("/sessions/{sid}/payment", response_model=CallSession)
def set_payment(sid: str, body: SetPaymentRequest, request: Request) -> CallSession:
    return mutate(request, sid, lambda cs: cs.model_copy(update={"payment": body.payment}))


@router.put("/sessions/{sid}/asked/{category}", response_model=CallSession)
def set_asked(sid: str, category: Category, request: Request) -> CallSession:
    if category not in UPSELL_ORDER:
        raise DomainError.of(
            "invalid_asked_category",
            f"Categoria „{category.value}” nu face parte din checklist.",
            field="category",
        )

    def apply(call_session: CallSession) -> CallSession:
        flags = {**call_session.asked_flags, category.value: True}
        return call_session.model_copy(update={"asked_flags": flags})

    return mutate(request, sid, apply)


@router.get("/sessions/{sid}/summary", response_model=OrderSummaryOut)
def get_order_summary(sid: str, request: Request, db_session: SessionDep) -> OrderSummaryOut:
    call_session = require_call_session(request, sid)
    eta = placement.estimate_eta(call_session, db_session, request.app.state.kitchen)
    return OrderSummaryOut(
        spoken_text=spoken.summary_text(call_session, eta),
        fulfillment=call_session.fulfillment,
        cart=call_session.cart,
        total_formatted=money.format_ron(call_session.cart.total_bani),
        address=call_session.address,
        eta=eta,
    )


@router.post("/sessions/{sid}/place", response_model=Order)
async def place_order(
    sid: str, body: PlaceOrderRequest, request: Request, db_session: SessionDep
) -> Order:
    """Plaseaza comanda si anunta dashboard-urile.

    Ruta e `async` doar pentru `hub.broadcast`. Tot restul — query-uri SQLite,
    `commit()` si `ORDER_ID_LOCK` — e sincron si blocant, iar o ruta `async` ruleaza
    direct pe event loop, spre deosebire de una sincrona pe care Starlette o trimite
    singura in thread-pool. Lasata asa, o plasare ar bloca fiecare alta cerere si
    difuzarea pe `/ws/orders` cat tine scrierea. De aceea munca blocanta merge
    explicit prin `run_in_threadpool`, iar pe event loop ramane doar difuzarea.
    """
    order, is_new = await run_in_threadpool(
        placement.place_order_blocking, sid, body, request, db_session
    )
    if not is_new:
        return order

    hub: EventHub = request.app.state.events
    await hub.broadcast(
        {
            "type": "order_created",
            "order_id": order.id,
            "status": order.status.value,
            "fulfillment": order.fulfillment.value,
        }
    )
    return order
