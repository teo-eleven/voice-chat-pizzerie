"""DTO-uri pentru corpul cererilor HTTP.

Domeniul isi expune modelele Pydantic direct la raspuns (`Cart`, `Order`,
`AddressResult`, ...) — API-ul e un strat subtire, nu le duplica. Schemele de
aici acopera doar cererile (care nu au echivalent in domeniu) si raspunsurile
compuse special pentru voce/ecran (meniul formatat, rezumatul comenzii).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from packages.domain.enums import Fulfillment, OrderStatus, PaymentMethod, SizeCode
from packages.domain.limits import (
    MAX_ADDRESS_TEXT_LEN,
    MAX_IDEMPOTENCY_KEY_LEN,
    MAX_NAME_LEN,
    MAX_PHONE_LEN,
    MAX_QTY_PER_LINE,
)
from packages.domain.models import Address, Cart, EtaWindow

#: Numarul de ingrediente scoase pe o linie e mic in practica; plafonat aici ca
#: sa nu se poata trimite un body absurd de mare. Nu e un plafon de domeniu —
#: e strict validare de intrare, la fel ca restul acestui fisier.
_MAX_REMOVED_INGREDIENTS = 20
#: Reutilizam plafonul de lungime al numelor pentru numele de ingrediente: text
#: liber scurt, de aceeasi natura.
_RemovedIngredient = Annotated[str, Field(max_length=MAX_NAME_LEN)]


class _ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- cereri


class AddItemRequest(_ApiModel):
    product_id: str
    qty: int = Field(default=1, gt=0, le=MAX_QTY_PER_LINE)
    size_code: SizeCode | None = None
    removed_ingredients: tuple[_RemovedIngredient, ...] = Field(
        default=(), max_length=_MAX_REMOVED_INGREDIENTS
    )


class UpdateItemRequest(_ApiModel):
    qty: int | None = Field(default=None, gt=0, le=MAX_QTY_PER_LINE)
    size_code: SizeCode | None = None
    removed_ingredients: tuple[_RemovedIngredient, ...] | None = Field(
        default=None, max_length=_MAX_REMOVED_INGREDIENTS
    )


class SetFulfillmentRequest(_ApiModel):
    fulfillment: Fulfillment


class ResolveAddressRequest(_ApiModel):
    #: Text liber rostit de client. Plafonat: `geocoding.parse_spoken_address`
    #: face backtracking pe regex peste el, iar un body nemarginit ar putea
    #: bloca un worker cu o singura cerere.
    text: str = Field(max_length=MAX_ADDRESS_TEXT_LEN)


class SetContactRequest(_ApiModel):
    phone: str = Field(max_length=MAX_PHONE_LEN, pattern=r"^\+?[0-9 .()-]{6,20}$")
    name: str = Field(default="", max_length=MAX_NAME_LEN)


class SetPaymentRequest(_ApiModel):
    payment: PaymentMethod


class PlaceOrderRequest(_ApiModel):
    idempotency_key: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_LEN)


class SetOrderStatusRequest(_ApiModel):
    status: OrderStatus


# --------------------------------------------------------------------------- raspunsuri


class MenuSizeOut(_ApiModel):
    code: SizeCode
    label: str
    diameter_cm: int
    price_bani: int
    price_formatted: str
    oven_slots: int
    bake_minutes: int


class MenuItemOut(_ApiModel):
    id: str
    name: str
    category: str
    description: str
    ingredients: tuple[str, ...]
    allergens: tuple[str, ...]
    sizes: tuple[MenuSizeOut, ...] = ()
    price_bani: int | None = None
    price_formatted: str | None = None
    prep_minutes: int
    available: bool
    aliases: tuple[str, ...]


class OrderSummaryOut(_ApiModel):
    """Rezumatul comenzii curente: text canonic pentru rostit, plus date structurate."""

    spoken_text: str
    fulfillment: Fulfillment
    cart: Cart
    total_formatted: str
    address: Address | None = None
    eta: EtaWindow


class KitchenOrderOut(_ApiModel):
    """Comanda vazuta pe ecranul de bucatarie: fara `contact` si fara `address`.

    Bucataria are nevoie de continut, alergii si status — nu de telefonul
    clientului sau de adresa completa cu interfon. `view=driver` continua sa
    primeasca `Order` intreg, pentru ca livratorul are nevoie operationala de
    adresa si telefon.
    """

    id: str
    #: Numarul rostit al comenzii — pe el il striga bucataria, nu id-ul intern.
    daily_number: int = 0
    status: OrderStatus
    fulfillment: Fulfillment
    cart: Cart
    eta: EtaWindow | None = None
    allergy_note: str | None = None
    created_at: datetime | None = None
