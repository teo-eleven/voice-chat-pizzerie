"""Starea apelului ca lista de bifat, nu ca sina de tren.

Agentul nu urmareste „la ce pas sunt", ci **ce am si ce imi lipseste**. Fiecare
categorie de upsell are doua steaguri independente:

- *intrebat?* — am propus-o deja (traieste pe server, in `asked_flags`)
- *are?*      — clientul chiar a luat ceva din ea (derivat din cos)

Se intreaba doar unde ambele sunt goale. Un „nu" inchide categoria definitiv:
serverul marcheaza *intrebat*, iar `pending_upsell` nu o mai propune. Fara asta,
un agent care isi tine starea in cap reintreaba „doriti si o bautura?" dupa ce
clientul tocmai a refuzat — cel mai enervant esec al unui bot de comenzi.

Singura reordonare fata de ordinea naturala: **tipul de livrare si adresa se afla
inaintea upsell-ului**, ca sa nu construim trei minute de comanda la o adresa
unde nu livram.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.api.sessions import CallSession
from packages.domain.enums import UPSELL_ORDER, Category, Fulfillment

#: Etichetele rostite ale categoriilor de upsell, in ordinea in care se propun.
_CATEGORY_LABELS: dict[Category, str] = {
    Category.SAUCE: "sosuri",
    Category.DRINK: "bauturi",
    Category.DESSERT: "desert",
}


@dataclass(frozen=True, slots=True)
class CategorySlot:
    category: Category
    label: str
    asked: bool
    has: bool

    @property
    def is_open(self) -> bool:
        """Deschisa = neintrebata si fara produs. Doar astea se propun."""
        return not self.asked and not self.has


@dataclass(frozen=True, slots=True)
class Checklist:
    """Ce are agentul si ce ii lipseste, la un moment dat din apel."""

    has_main_product: bool
    upsell: tuple[CategorySlot, ...]
    fulfillment: Fulfillment
    has_address: bool
    has_phone: bool
    has_name: bool
    has_payment: bool
    order_placed: bool

    @property
    def needs_address(self) -> bool:
        return self.fulfillment == Fulfillment.DELIVERY and not self.has_address

    @property
    def pending_upsell(self) -> CategorySlot | None:
        """Urmatoarea categorie de propus, sau `None` daca upsell-ul s-a incheiat."""
        return next((slot for slot in self.upsell if slot.is_open), None)

    @property
    def missing_required(self) -> tuple[str, ...]:
        """Ce lipseste ca sa se poata plasa comanda, in ordinea in care se cere."""
        missing: list[str] = []
        if not self.has_main_product:
            missing.append("cel putin un produs")
        if self.needs_address:
            missing.append("adresa de livrare")
        if not self.has_phone:
            missing.append("telefon")
        if not self.has_name:
            missing.append("prenume")
        if not self.has_payment:
            missing.append("metoda de plata")
        return tuple(missing)

    @property
    def ready_to_place(self) -> bool:
        return not self.missing_required and not self.order_placed


def from_session(session: CallSession) -> Checklist:
    """Deriva checklist-ul din starea reala a sesiunii — nu din memoria agentului."""
    categories_in_cart = {line.category for line in session.cart.lines}
    return Checklist(
        has_main_product=Category.PIZZA in categories_in_cart,
        upsell=tuple(
            CategorySlot(
                category=category,
                label=_CATEGORY_LABELS[category],
                asked=bool(session.asked_flags.get(category.value, False)),
                has=category in categories_in_cart,
            )
            for category in UPSELL_ORDER
        ),
        fulfillment=session.fulfillment,
        has_address=session.address is not None,
        has_phone=session.contact is not None and bool(session.contact.phone),
        has_name=session.contact is not None and bool(session.contact.name),
        has_payment=session.payment is not None,
        order_placed=session.placed_order_id is not None,
    )


def render(checklist: Checklist) -> str:
    """Blocul injectat in context la fiecare tur, ca agentul sa nu ghiceasca starea.

    Deliberat compact si fara verdicte: enumera fapte, nu instructiuni. Ce se face
    cu ele e treaba promptului de sistem — daca aici ar scrie „acum intreaba X",
    checklist-ul ar redeveni sina de tren.
    """
    lines = [
        f"produse principale: {_mark(checklist.has_main_product)}",
    ]
    lines += [
        f"{slot.label}: intrebat {_mark(slot.asked)} / are {_mark(slot.has)}"
        for slot in checklist.upsell
    ]
    lines.append(f"tip preluare: {checklist.fulfillment.value}")
    if checklist.fulfillment == Fulfillment.DELIVERY:
        lines.append(f"adresa confirmata in zona: {_mark(checklist.has_address)}")
    lines += [
        f"telefon: {_mark(checklist.has_phone)}",
        f"prenume: {_mark(checklist.has_name)}",
        f"plata: {_mark(checklist.has_payment)}",
    ]

    if checklist.order_placed:
        lines.append("comanda: PLASATA (nu se mai modifica)")
    elif checklist.missing_required:
        lines.append("mai lipseste pentru plasare: " + ", ".join(checklist.missing_required))
    else:
        lines.append("se poate plasa dupa confirmarea explicita a clientului")

    upsell = checklist.pending_upsell
    lines.append(
        f"upsell neatins: {upsell.label}" if upsell else "upsell: toate categoriile inchise"
    )
    return "Stare curenta pe server (autoritativa):\n" + "\n".join(f"- {line}" for line in lines)


def _mark(value: bool) -> str:
    return "da" if value else "nu"
