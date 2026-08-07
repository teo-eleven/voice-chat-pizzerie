"""Rute care oglindesc 1:1 tool-urile agentului vocal (Faza 2) pe sesiunea de apel."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from packages.domain import capacity, cart_ops, delivery_zone, money, pricing
from packages.domain.catalog import Catalog
from packages.domain.enums import (
    UPSELL_ORDER,
    AddressResolution,
    Category,
    Fulfillment,
    OrderStatus,
)
from packages.domain.errors import DomainError
from packages.domain.models import (
    Address,
    AddressResult,
    Cart,
    CartLine,
    Contact,
    EtaWindow,
    KitchenConfig,
    Order,
    ZoneConfig,
)

from . import geocoding
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
from .sessions import CallSession, SessionStore
from .tables import ORDER_ID_LOCK, OrderRow, from_domain, next_order_id, to_domain

router = APIRouter(prefix="/api", tags=["sessions"])

#: Cate incercari face `_persist_order` la coliziune pe cheia primara `id`
#: (doua plasari concurente care au calculat acelasi id) inainte sa renunte.
_MAX_PLACE_ATTEMPTS = 3


def _require_call_session(request: Request, sid: str) -> CallSession:
    store: SessionStore = request.app.state.sessions
    call_session = store.get(sid)
    if call_session is None:
        raise HTTPException(status_code=404, detail=f"Sesiunea „{sid}” nu exista.")
    return call_session


def _mutate(
    request: Request, sid: str, fn: Callable[[CallSession], CallSession]
) -> CallSession:
    """Aplica `fn` atomic prin `SessionStore.update` (citire -> aplicare -> scriere
    sub lock), ca doua request-uri paralele pe aceeasi sesiune sa nu se piarda."""
    store: SessionStore = request.app.state.sessions
    updated = store.update(sid, fn)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Sesiunea „{sid}” nu exista.")
    return updated


@router.post("/sessions")
def create_session(request: Request) -> dict[str, str]:
    store: SessionStore = request.app.state.sessions
    return {"session_id": store.create().session_id}


@router.get("/sessions/{sid}", response_model=CallSession)
def get_session_state(sid: str, request: Request) -> CallSession:
    return _require_call_session(request, sid)


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
            qty=body.qty,
            size_code=body.size_code,
            removed_ingredients=body.removed_ingredients,
            fulfillment=call_session.fulfillment,
            zone=zone,
        )
        return call_session.model_copy(update={"cart": cart})

    return _mutate(request, sid, apply).cart


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
            qty=body.qty,
            size_code=body.size_code,
            removed_ingredients=body.removed_ingredients,
            fulfillment=call_session.fulfillment,
            zone=zone,
        )
        return call_session.model_copy(update={"cart": cart})

    return _mutate(request, sid, apply).cart


@router.delete("/sessions/{sid}/items/{line_id}", response_model=Cart)
def remove_item(sid: str, line_id: str, request: Request) -> Cart:
    zone: ZoneConfig = request.app.state.zone

    def apply(call_session: CallSession) -> CallSession:
        cart = cart_ops.remove_line(
            call_session.cart,
            line_id,
            fulfillment=call_session.fulfillment,
            zone=zone,
        )
        return call_session.model_copy(update={"cart": cart})

    return _mutate(request, sid, apply).cart


@router.put("/sessions/{sid}/fulfillment", response_model=Cart)
def set_fulfillment(sid: str, body: SetFulfillmentRequest, request: Request) -> Cart:
    zone: ZoneConfig = request.app.state.zone

    def apply(call_session: CallSession) -> CallSession:
        cart = pricing.price_cart(call_session.cart.lines, body.fulfillment, zone)
        return call_session.model_copy(update={"fulfillment": body.fulfillment, "cart": cart})

    return _mutate(request, sid, apply).cart


@router.post("/sessions/{sid}/address", response_model=AddressResult)
def resolve_address(sid: str, body: ResolveAddressRequest, request: Request) -> AddressResult:
    # Confirma ca sesiunea exista (si ii marcheaza accesul) inainte de geocodare.
    _require_call_session(request, sid)
    candidates = geocoding.geocode(body.text, request.app.state.addresses)
    result = delivery_zone.resolve_from_candidates(candidates, request.app.state.zone)
    if result.resolution == AddressResolution.OK:
        address = result.candidates[0].address
        _mutate(request, sid, lambda cs: cs.model_copy(update={"address": address}))
    return result


@router.put("/sessions/{sid}/contact", response_model=CallSession)
def set_contact(sid: str, body: SetContactRequest, request: Request) -> CallSession:
    contact = Contact(phone=body.phone, name=body.name)
    return _mutate(request, sid, lambda cs: cs.model_copy(update={"contact": contact}))


@router.put("/sessions/{sid}/payment", response_model=CallSession)
def set_payment(sid: str, body: SetPaymentRequest, request: Request) -> CallSession:
    return _mutate(request, sid, lambda cs: cs.model_copy(update={"payment": body.payment}))


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

    return _mutate(request, sid, apply)


@router.get("/sessions/{sid}/summary", response_model=OrderSummaryOut)
def get_order_summary(sid: str, request: Request, db_session: SessionDep) -> OrderSummaryOut:
    call_session = _require_call_session(request, sid)
    eta = _estimate_eta(call_session, db_session, request.app.state.kitchen)
    return OrderSummaryOut(
        spoken_text=_spoken_summary(call_session, eta),
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
    existing = _find_by_idempotency_key(db_session, body.idempotency_key)
    if existing is not None:
        return to_domain(existing)

    call_session = _require_call_session(request, sid)
    zone = request.app.state.zone
    kitchen_config = request.app.state.kitchen
    _validate_for_placement(call_session, request.app.state.catalog, zone)
    eta = _estimate_eta(call_session, db_session, kitchen_config)
    overload = capacity.overload_issue(eta, kitchen_config)
    if overload is not None:
        raise DomainError(overload)

    order_row = _persist_order(db_session, call_session, eta, body.idempotency_key)
    # Doar campul `placed_order_id` se muta; restul sesiunii ramane cel mai recent
    # (`store.update` citeste starea curenta sub lock, nu instantanteul de la inceputul
    # rutei), ca o mutatie de cos concurenta sa nu fie stearsa de acest ultim pas.
    store: SessionStore = request.app.state.sessions
    store.update(sid, lambda cs: cs.model_copy(update={"placed_order_id": order_row.id}))

    hub: EventHub = request.app.state.events
    await hub.broadcast(
        {
            "type": "order_created",
            "order_id": order_row.id,
            "status": order_row.status.value,
            "fulfillment": order_row.fulfillment.value,
        }
    )
    return to_domain(order_row)


def _find_by_idempotency_key(db_session: Session, idempotency_key: str) -> OrderRow | None:
    statement = select(OrderRow).where(OrderRow.idempotency_key == idempotency_key)
    return db_session.exec(statement).first()


def _persist_order(
    db_session: Session, call_session: CallSession, eta: EtaWindow, idempotency_key: str
) -> OrderRow:
    """Aloca id-ul si insereaza comanda, sub `ORDER_ID_LOCK`, cu reincercare la
    coliziune pe `id`.

    Coliziunea pe `idempotency_key` inseamna ca cererea a mai fost procesata:
    se returneaza randul deja existent (comportament idempotent). Coliziunea
    pe `id` (doua plasari concurente care au calculat acelasi maxim inainte ca
    vreuna sa faca commit) e o eroare de infrastructura, nu un refuz de
    business — contractul aplicatiei e "niciodata 500 pentru asta" — asa ca se
    reincearca cu un id recalculat, de cel mult `_MAX_PLACE_ATTEMPTS` ori.
    """
    with ORDER_ID_LOCK:
        last_error: IntegrityError | None = None
        for _attempt in range(_MAX_PLACE_ATTEMPTS):
            order_id = next_order_id(db_session)
            order = _build_order(order_id, call_session, eta, idempotency_key)
            row = from_domain(order)
            db_session.add(row)
            try:
                db_session.commit()
            except IntegrityError as exc:
                db_session.rollback()
                existing = _find_by_idempotency_key(db_session, idempotency_key)
                if existing is not None:
                    return existing
                last_error = exc
                continue
            return row
        assert last_error is not None
        raise last_error


def _build_order(
    order_id: str, call_session: CallSession, eta: EtaWindow, idempotency_key: str
) -> Order:
    assert call_session.contact is not None
    assert call_session.payment is not None
    return Order(
        id=order_id,
        cart=call_session.cart,
        fulfillment=call_session.fulfillment,
        contact=call_session.contact,
        payment=call_session.payment,
        status=OrderStatus.NEW,
        address=call_session.address,
        eta=eta,
        allergy_note=call_session.allergy_note,
        created_at=datetime.now(UTC),
        idempotency_key=idempotency_key,
    )


def _validate_for_placement(call_session: CallSession, catalog: Catalog, zone: ZoneConfig) -> None:
    """Re-valideaza tot de la zero, in ordinea ceruta, si se opreste la primul refuz."""
    if call_session.cart.is_empty:
        raise DomainError.of("empty_cart", "Coșul este gol. Adăugați cel puțin un produs.")
    for line in call_session.cart.lines:
        product = catalog.by_id(line.product_id)
        if product is None or not product.available:
            raise DomainError.of(
                "product_unavailable", f"„{line.product_name}” nu mai este disponibil."
            )
    if call_session.fulfillment == Fulfillment.DELIVERY:
        _validate_delivery_address(call_session.address, zone)
    issue = pricing.check_minimum(call_session.cart, call_session.fulfillment, zone)
    if issue is not None:
        raise DomainError(issue)
    if call_session.contact is None:
        raise DomainError.of("contact_required", "Datele de contact nu au fost setate.")
    if call_session.payment is None:
        raise DomainError.of("payment_required", "Metoda de plata nu a fost setata.")


def _validate_delivery_address(address: Address | None, zone: ZoneConfig) -> None:
    if address is None:
        raise DomainError.of("address_required", "Adresa de livrare nu a fost setata.")
    if not delivery_zone.is_in_zone(address, zone):
        raise DomainError.of("address_out_of_zone", "Adresa nu mai este in zona de livrare.")


def _estimate_eta(
    call_session: CallSession, db_session: Session, kitchen_config: KitchenConfig
) -> EtaWindow:
    return capacity.estimate_eta(
        call_session.cart.lines,
        pending_slot_minutes=_pending_slot_minutes(db_session),
        fulfillment=call_session.fulfillment,
        config=kitchen_config,
    )


def _pending_slot_minutes(db_session: Session) -> int:
    active = (OrderStatus.NEW, OrderStatus.IN_KITCHEN)
    statement = select(OrderRow).where(OrderRow.status.in_(active))
    rows = db_session.exec(statement).all()
    queue = tuple(Cart.model_validate_json(row.cart_json).lines for row in rows)
    return capacity.queue_slot_minutes(queue)


def _spoken_summary(call_session: CallSession, eta: EtaWindow) -> str:
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
