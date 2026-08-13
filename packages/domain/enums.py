"""Enum-urile domeniului. Valorile string sunt stabile — intră în DB și în JSON."""

from enum import StrEnum


class Category(StrEnum):
    """Categoriile de produse. Ordinea contează: e ordinea de upsell a agentului."""

    PIZZA = "pizza"
    SAUCE = "sauce"
    DRINK = "drink"
    DESSERT = "dessert"


#: Ordinea în care agentul propune categoriile după produsele principale.
UPSELL_ORDER: tuple[Category, ...] = (Category.SAUCE, Category.DRINK, Category.DESSERT)


class SizeCode(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class Allergen(StrEnum):
    GLUTEN = "gluten"
    LACTOSE = "lactoza"
    EGG = "ou"
    NUTS = "nuci"
    FISH = "peste"
    SOY = "soia"
    MUSTARD = "mustar"


class Fulfillment(StrEnum):
    DELIVERY = "delivery"
    PICKUP = "pickup"


class PaymentMethod(StrEnum):
    CASH = "cash"
    #: Card la livrare prin POS-ul livratorului sau la ridicare. NICIODATĂ card dictat.
    CARD_ON_DELIVERY = "card_on_delivery"


class OrderStatus(StrEnum):
    NEW = "new"
    IN_KITCHEN = "in_kitchen"
    READY = "ready"
    ASSIGNED = "assigned"
    OUT = "out"
    DELIVERED = "delivered"
    PICKED_UP = "picked_up"
    CANCELLED = "cancelled"


class AddressResolution(StrEnum):
    """Rezultatul încercării de a rezolva o adresă rostită."""

    OK = "ok"
    AMBIGUOUS = "ambiguous"
    OUT_OF_ZONE = "out_of_zone"
    NOT_FOUND = "not_found"
    BELOW_MINIMUM = "below_minimum"


class EscalationReason(StrEnum):
    """Motivele pentru care apelul trece la un om.

    SCHELET FAZA 5, folosit deocamdată doar de `Escalation`. Lista vine direct din
    tabelul de situații neprevăzute din `docs/PLAN.md`.
    """

    LOW_CONFIDENCE = "low_confidence"
    ANGRY_CUSTOMER = "angry_customer"
    COMPLAINT = "complaint"
    ALLERGY = "allergy"
    LARGE_ORDER = "large_order"
    SCHEDULED_ORDER = "scheduled_order"
    CUSTOMER_REQUEST = "customer_request"
    PROVIDER_FAILURE = "provider_failure"
