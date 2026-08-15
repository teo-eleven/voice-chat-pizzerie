"""Modele imutabile ale domeniului.

Contractul comun pentru tot restul codului. Fără I/O, fără dependențe de framework.

Banii se țin **întotdeauna** ca întregi în bani (1 leu = 100 bani). Nicio operație
monetară nu folosește float. Formatarea pentru afișare/rostire e în `money.py`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    AddressResolution,
    Allergen,
    Category,
    EscalationReason,
    Fulfillment,
    OrderStatus,
    PaymentMethod,
    SizeCode,
)
from .limits import (
    MAX_ALLERGY_NOTE_LEN,
    MAX_IDEMPOTENCY_KEY_LEN,
    MAX_NAME_LEN,
    MAX_NOTES_LEN,
    MAX_PHONE_LEN,
)


class Frozen(BaseModel):
    """Bază imutabilă: orice modificare produce un obiect nou."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------- catalog


class SizeOption(Frozen):
    """O mărime de pizza, cu costul ei în cuptor."""

    code: SizeCode
    label: str
    diameter_cm: int
    price_bani: int = Field(gt=0)
    #: Cât ocupă în cuptor. Baza calculului de ETA.
    oven_slots: int = Field(gt=0)
    bake_minutes: int = Field(gt=0)


class Product(Frozen):
    id: str
    name: str
    category: Category
    description: str = ""
    ingredients: tuple[str, ...] = ()
    allergens: tuple[Allergen, ...] = ()
    #: Doar la pizza. Gol pentru celelalte categorii.
    sizes: tuple[SizeOption, ...] = ()
    #: Doar la produsele fără mărimi (sos, băutură, desert).
    price_bani: int | None = Field(default=None, gt=0)
    #: Minute de pregătire în afara cuptorului (deserturi, băuturi scoase din frigider).
    prep_minutes: int = 0
    available: bool = True
    #: Sinonime rostite de clienți: „capriciosa", „capricioza", „quattro".
    aliases: tuple[str, ...] = ()

    @property
    def has_sizes(self) -> bool:
        return bool(self.sizes)

    def size(self, code: SizeCode) -> SizeOption | None:
        return next((s for s in self.sizes if s.code == code), None)


class KitchenConfig(Frozen):
    """Capacitatea bucătăriei. Sursa calculului de ETA."""

    #: Câte sloturi de pizza încap simultan în cuptor.
    oven_slots: int = Field(gt=0)
    #: Minute de pregătire fixe per comandă (asamblare, ambalare).
    order_overhead_minutes: int = Field(ge=0)
    #: Marjă adăugată la final, ca să nu promitem la limită.
    safety_buffer_minutes: int = Field(ge=0)
    #: Rotunjim intervalul comunicat la multiplu de atâtea minute.
    quote_rounding_minutes: int = Field(gt=0)
    #: Lățimea intervalului rostit clientului („30-40 de minute").
    quote_window_minutes: int = Field(gt=0)
    #: Peste acest ETA, agentul nu mai preia comenzi și spune adevărul.
    max_promisable_minutes: int = Field(gt=0)
    #: Minute de drum estimate pentru livrare, adăugate peste timpul de bucătărie.
    delivery_drive_minutes: int = Field(ge=0)


class ZoneConfig(Frozen):
    """Zona de livrare și regulile ei comerciale."""

    #: Poligon [(lat, lon), ...]. Minim 3 puncte.
    polygon: tuple[tuple[float, float], ...]
    min_order_bani: int = Field(ge=0)
    delivery_fee_bani: int = Field(ge=0)
    free_delivery_threshold_bani: int | None = Field(default=None, gt=0)


class PricingContext(Frozen):
    """Contextul de calcul al prețului: `fulfillment` și `zone` merg mereu împreună."""

    fulfillment: Fulfillment
    zone: ZoneConfig


class LineSpec(Frozen):
    """Ce se adaugă ca linie nouă în coș. Toate câmpurile au valori implicite."""

    qty: int = 1
    size_code: SizeCode | None = None
    removed_ingredients: tuple[str, ...] = ()


class LineChanges(Frozen):
    """Ce se schimbă la o linie existentă. `None` înseamnă „păstrează valoarea actuală”."""

    qty: int | None = None
    size_code: SizeCode | None = None
    removed_ingredients: tuple[str, ...] | None = None


# --------------------------------------------------------------------------- coș


class CartLine(Frozen):
    """O linie de coș. `total_bani` e calculat de `pricing`, niciodată de LLM."""

    line_id: str
    product_id: str
    product_name: str
    category: Category
    qty: int = Field(gt=0)
    size_code: SizeCode | None = None
    size_label: str | None = None
    #: Ingrediente scoase la cererea clientului („fără ceapă").
    removed_ingredients: tuple[str, ...] = ()
    unit_price_bani: int = Field(gt=0)
    total_bani: int = Field(gt=0)
    oven_slots: int = 0
    bake_minutes: int = 0
    prep_minutes: int = 0


class Cart(Frozen):
    lines: tuple[CartLine, ...] = ()
    items_bani: int = 0
    delivery_fee_bani: int = 0
    total_bani: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.lines

    def has_category(self, category: Category) -> bool:
        """Folosit de checklist: nu întrebăm de băuturi dacă are deja băuturi."""
        return any(line.category == category for line in self.lines)


# --------------------------------------------------------------------------- adresă


class Address(Frozen):
    """Adresă structurată. Sub-câmpurile se confirmă separat — pe voce se pierd ușor."""

    street: str
    number: str
    block: str | None = None
    staircase: str | None = None
    floor: str | None = None
    apartment: str | None = None
    intercom: str | None = None
    #: Detalii pentru livrator: „câine în curte", „poarta a doua".
    notes: str | None = Field(default=None, max_length=MAX_NOTES_LEN)
    lat: float | None = None
    lon: float | None = None
    formatted: str = ""


class AddressCandidate(Frozen):
    address: Address
    confidence: float = Field(ge=0.0, le=1.0)
    in_zone: bool


class AddressResult(Frozen):
    """Rezultatul lui `resolve_address`. Decizia e a backend-ului, nu a LLM-ului."""

    resolution: AddressResolution
    candidates: tuple[AddressCandidate, ...] = ()
    message: str = ""

    @property
    def is_usable(self) -> bool:
        return self.resolution == AddressResolution.OK and len(self.candidates) == 1


# --------------------------------------------------------------------------- comandă


class Contact(Frozen):
    #: La telefonie vine din caller ID și se confirmă, nu se dictează.
    phone: str = Field(max_length=MAX_PHONE_LEN)
    #: Prenumele e suficient.
    name: str = Field(default="", max_length=MAX_NAME_LEN)


class EtaWindow(Frozen):
    """Interval rotunjit în sus. Nu promitem niciodată un minut exact."""

    min_minutes: int = Field(ge=0)
    max_minutes: int = Field(ge=0)

    def spoken(self) -> str:
        return f"{self.min_minutes}-{self.max_minutes} de minute"


class Order(Frozen):
    #: Identitatea comenzii, unică peste tot istoricul. Nu se rostește.
    id: str
    #: Numărul rostit — „numărul comenzii 7" — repornit de la 1 în fiecare zi de
    #: lucru. Se repetă de la o zi la alta, deci nu identifică nimic singur; e
    #: pentru oameni: clientul îl reține, bucătarul îl strigă.
    daily_number: int = Field(default=0, ge=0)
    cart: Cart
    fulfillment: Fulfillment
    contact: Contact
    payment: PaymentMethod
    status: OrderStatus = OrderStatus.NEW
    #: Obligatorie la livrare, absentă la ridicare.
    address: Address | None = None
    eta: EtaWindow | None = None
    #: Text liber marcat vizibil în bucătărie. Prezența lui forțează escaladarea.
    allergy_note: str | None = Field(default=None, max_length=MAX_ALLERGY_NOTE_LEN)
    created_at: datetime | None = None
    #: Garantează că un retry de rețea nu produce comandă dublă.
    idempotency_key: str = Field(default="", max_length=MAX_IDEMPOTENCY_KEY_LEN)

    @property
    def needs_driver(self) -> bool:
        return self.fulfillment == Fulfillment.DELIVERY


class Escalation(Frozen):
    """Predarea apelului către un operator uman.

    SCHELET FAZA 5, neconectat încă la nicio rută. `docs/PLAN.md` numește escaladarea
    „plasa de siguranță": confidence mic de două ori pe același câmp, client nervos,
    reclamație, alergie, comandă mare. Modelul e definit de pe acum pentru că motivele
    sunt decizii de produs deja luate (vezi `EscalationReason`), nu detalii de
    implementare — nu se rediscută când se scrie faza.
    """

    reason: EscalationReason
    detail: str = ""
    #: Rezumatul conversației, predat operatorului uman.
    summary: str = ""


class ValidationIssue(Frozen):
    """Motiv structurat de refuz. LLM-ul îl povestește, nu îl inventează."""

    code: str
    message: str
    field: str | None = None
