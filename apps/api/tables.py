"""Tabelul de persistenta pentru comenzi, separat de modelele de domeniu.

`Order` (domeniu) e imutabil si nu are treaba cu SQL. `OrderRow` e randul din
baza: cosul, adresa si contactul se serializeaza ca JSON intr-o coloana text,
restul campurilor interogate direct de dashboard-uri (bucatarie/livrator) au
coloane native.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, cast, func
from sqlmodel import Field, Session, SQLModel, col, select

from packages.domain.enums import Fulfillment, OrderStatus, PaymentMethod
from packages.domain.models import Address, Cart, Contact, EtaWindow, Order

#: Serializeaza generarea id-ului de comanda intre thread-uri: doua plasari
#: concurente nu trebuie sa citeasca acelasi maxim si sa calculeze acelasi id
#: inainte ca vreuna dintre ele sa fi facut commit.
ORDER_ID_LOCK = threading.Lock()

#: Prefixul id-ului de comanda. Sufixul numeric de 4 cifre vine dupa el.
_ORDER_ID_PREFIX = "CMD-"

#: Ceasul dupa care se rupe ziua de lucru: cel al pizzeriei, nu UTC. Fara el,
#: comenzile dintre miezul noptii si ora 3 ar primi numarul zilei precedente.
PIZZERIA_TIMEZONE = ZoneInfo("Europe/Bucharest")


class OrderRow(SQLModel, table=True):
    """Randul de comanda persistat. `to_domain`/`from_domain` fac conversia."""

    id: str = Field(primary_key=True)
    status: OrderStatus
    fulfillment: Fulfillment
    payment: PaymentMethod
    idempotency_key: str = Field(unique=True, index=True)
    created_at: datetime
    #: Ziua de lucru („2026-08-14"), dupa ceasul local. Indexata: numarul urmator se
    #: cauta filtrand pe ea la fiecare plasare.
    business_date: str = Field(default="", index=True)
    #: Numarul rostit al comenzii, repornit de la 1 in fiecare zi. NU e unic peste
    #: istoricul complet — identitatea comenzii ramane `id`.
    daily_number: int = Field(default=0)
    eta_min: int
    eta_max: int
    allergy_note: str | None = None
    #: JSON serializat: coșul complet, cu liniile lui.
    cart_json: str
    #: JSON serializat, absent la ridicare.
    address_json: str | None = None
    contact_json: str


def from_domain(order: Order) -> OrderRow:
    """`Order` de domeniu -> rand persistabil. Comanda plasata are deja ETA si created_at.

    Verificarile sunt `ValueError`, nu `assert`: un `assert` dispare sub `python -O`,
    iar atunci `order.eta.min_minutes` ar da un `AttributeError` obscur in loc de un
    mesaj clar. Nu sunt `DomainError` — clientul nu are ce corecta aici, e defect al
    nostru daca o comanda ajunge la persistenta fara ETA.
    """
    if order.eta is None:
        raise ValueError(f"Comanda „{order.id}” ajunge la persistenta fara ETA calculat.")
    if order.created_at is None:
        raise ValueError(f"Comanda „{order.id}” ajunge la persistenta fara created_at.")
    return OrderRow(
        id=order.id,
        status=order.status,
        fulfillment=order.fulfillment,
        payment=order.payment,
        idempotency_key=order.idempotency_key,
        created_at=order.created_at,
        business_date=business_date_of(order.created_at),
        daily_number=order.daily_number,
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
        daily_number=row.daily_number,
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

    Maximul se calculeaza in SQL, nu in Python: varianta care incarca toate id-urile
    si le compara aici crestea liniar cu istoricul comenzilor, la fiecare plasare, si
    o facea sub `ORDER_ID_LOCK` — adica exact intervalul in care nicio alta plasare nu
    poate avansa. Comparatia e pe sufixul convertit la intreg, nu pe textul intreg:
    lexicografic, `CMD-10000` ar veni inaintea lui `CMD-9999` si am recicla un id.

    ATENTIE, depinde de SQLite: `CAST('abcd' AS INTEGER)` da `0` aici, deci un id cu
    format neasteptat e ignorat linistit. Pe Postgres sau MySQL aceeasi expresie ridica
    eroare la runtime. Daca `DATABASE_URL` pleaca vreodata de la SQLite, functia asta
    trebuie rescrisa, nu doar reconfigurata.
    """
    statement = select(
        func.max(cast(func.substr(OrderRow.id, len(_ORDER_ID_PREFIX) + 1), Integer))
    )
    max_suffix = session.exec(statement).one() or 0
    return f"{_ORDER_ID_PREFIX}{max_suffix + 1:04d}"


def business_date_of(moment: datetime) -> str:
    """Ziua de lucru a unui moment, „AAAA-LL-ZZ", dupa ceasul pizzeriei.

    Un moment fara fus orar se citeste ca UTC: asa vin inapoi randurile scrise de
    versiunile anterioare, iar o comanda veche n-are voie sa arunce la citire.
    """
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(PIZZERIA_TIMEZONE).strftime("%Y-%m-%d")


def next_daily_number(session: Session, business_date: str) -> int:
    """Numarul rostit al urmatoarei comenzi din ziua data: 1, 2, 3, ...

    Din maximul zilei, nu din `COUNT(*)`: dupa stergerea unei comenzi, un contor pe
    numar de randuri ar da doua comenzi cu acelasi numar rostit in aceeasi zi, adica
    exact confuzia pe care numarul trebuie s-o previna la ghiseu.

    Se cheama sub `ORDER_ID_LOCK`, ca si `next_order_id`: doua plasari concurente
    nu trebuie sa citeasca acelasi maxim inainte ca vreuna sa fi facut commit.
    """
    statement = select(func.max(OrderRow.daily_number)).where(
        col(OrderRow.business_date) == business_date
    )
    return (session.exec(statement).one() or 0) + 1
