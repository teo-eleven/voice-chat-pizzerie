"""Plasarea comenzii: re-validare completa, ETA, alocare de id si persistenta.

Separat de `routes_session` pentru ca e singura bucata din stratul API care are
reguli proprii, nu doar orchestrare de HTTP: `docs/PLAN.md` cere ca `place_order` sa
re-valideze tot de la zero (stoc, zona, pret, minim) si sa fie idempotent. Ruta care
il cheama ramane subtire — primeste `(comanda, e_noua)` si difuzeaza evenimentul.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from packages.domain import capacity, delivery_zone, pricing
from packages.domain.catalog import Catalog
from packages.domain.enums import Fulfillment, OrderStatus
from packages.domain.errors import DomainError
from packages.domain.models import (
    Address,
    Cart,
    EtaWindow,
    KitchenConfig,
    Order,
    ZoneConfig,
)

from .schemas import PlaceOrderRequest
from .session_access import require_call_session
from .sessions import CallSession, SessionStore
from .tables import ORDER_ID_LOCK, OrderRow, from_domain, next_order_id, to_domain

#: Cate incercari face `persist_order` la coliziune pe cheia primara `id`
#: (doua plasari concurente care au calculat acelasi id) inainte sa renunte.
_MAX_PLACE_ATTEMPTS = 3


def place_order_blocking(
    sid: str, body: PlaceOrderRequest, request: Request, db_session: Session
) -> tuple[Order, bool]:
    """Partea sincrona a plasarii, rulata in thread-pool de ruta care o cheama.

    Intoarce `(comanda, e_noua)`. `e_noua=False` inseamna reluare idempotenta a unei
    cereri deja procesate: comanda exista deja, deci nu se mai emite un al doilea
    eveniment `order_created` catre dashboard-uri.

    Conversia `to_domain` se face tot aici, nu la apelant: citirea atributelor unui rand
    dupa `commit()` poate declansa un refresh, adica inca un query — care ar ajunge
    inapoi pe event loop, fix ce incearca ruta sa evite.
    """
    existing = _find_by_idempotency_key(db_session, body.idempotency_key)
    if existing is not None:
        return to_domain(existing), False

    call_session = require_call_session(request, sid)
    zone = request.app.state.zone
    kitchen_config = request.app.state.kitchen
    validate_for_placement(call_session, request.app.state.catalog, zone)
    eta = estimate_eta(call_session, db_session, kitchen_config)
    overload = capacity.overload_issue(eta, kitchen_config)
    if overload is not None:
        raise DomainError(overload)

    order_row, inserted = _persist_order(db_session, call_session, eta, body.idempotency_key)
    # Doar campul `placed_order_id` se muta; restul sesiunii ramane cel mai recent
    # (`store.update` citeste starea curenta sub lock, nu instantanteul de la inceputul
    # rutei), ca o mutatie de cos concurenta sa nu fie stearsa de acest ultim pas.
    store: SessionStore = request.app.state.sessions
    store.update(sid, lambda cs: cs.model_copy(update={"placed_order_id": order_row.id}))
    return to_domain(order_row), inserted


def _find_by_idempotency_key(db_session: Session, idempotency_key: str) -> OrderRow | None:
    statement = select(OrderRow).where(OrderRow.idempotency_key == idempotency_key)
    return db_session.exec(statement).first()


def _persist_order(
    db_session: Session, call_session: CallSession, eta: EtaWindow, idempotency_key: str
) -> tuple[OrderRow, bool]:
    """Aloca id-ul si insereaza comanda, sub `ORDER_ID_LOCK`, cu reincercare la
    coliziune pe `id`.

    Intoarce `(rand, a_fost_inserat)`. `a_fost_inserat=False` inseamna ca alta cerere
    cu aceeasi cheie de idempotenta a ajuns prima la commit, iar noi am recuperat randul
    ei. Distinctia conteaza pentru apelant: fara ea, a doua cerere dintr-un retry aproape
    simultan ar difuza inca un `order_created` pentru o comanda deja anuntata, si aceeasi
    comanda ar aparea de doua ori pe ecranul bucatariei.

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
                    return existing, False
                last_error = exc
                continue
            return row, True
        if last_error is None:  # pragma: no cover - bucla ruleaza cel putin o data
            raise ValueError("Plasarea a esuat fara sa inregistreze vreo eroare de integritate.")
        raise last_error


def _build_order(
    order_id: str, call_session: CallSession, eta: EtaWindow, idempotency_key: str
) -> Order:
    # Garantat de `validate_for_placement`, chemata inainte. Verificat totusi explicit:
    # un `assert` dispare sub `python -O`, iar `Order` ar primi `None` pe campuri
    # obligatorii. `ValueError`, nu `DomainError` — daca se declanseaza, ordinea
    # verificarilor s-a rupt la noi in cod, nu clientul a trimis ceva gresit.
    contact = call_session.contact
    payment = call_session.payment
    if contact is None:
        raise ValueError(f"Sesiunea „{call_session.session_id}” ajunge la plasare fara contact.")
    if payment is None:
        raise ValueError(
            f"Sesiunea „{call_session.session_id}” ajunge la plasare fara metoda de plata."
        )
    return Order(
        id=order_id,
        cart=call_session.cart,
        fulfillment=call_session.fulfillment,
        contact=contact,
        payment=payment,
        status=OrderStatus.NEW,
        address=call_session.address,
        eta=eta,
        allergy_note=call_session.allergy_note,
        created_at=datetime.now(UTC),
        idempotency_key=idempotency_key,
    )


def validate_for_placement(call_session: CallSession, catalog: Catalog, zone: ZoneConfig) -> None:
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


def estimate_eta(
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
    # `col()` spune verificatorului de tipuri ca `OrderRow.status` e o coloana SQL, nu
    # o valoare `OrderStatus` — fara el, `.in_()` pare un atribut inexistent pe enum.
    statement = select(OrderRow).where(col(OrderRow.status).in_(active))
    rows = db_session.exec(statement).all()
    queue = tuple(Cart.model_validate_json(row.cart_json).lines for row in rows)
    return capacity.queue_slot_minutes(queue)
