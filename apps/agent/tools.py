"""Contractul de tool-uri: interfata dintre LLM si backend.

Fiecare tool oglindeste o ruta din Faza 1. LLM-ul nu calculeaza niciodata un pret,
nu decide daca livram undeva si nu inventeaza un produs — cere backend-ului si
povesteste raspunsul. Halucinarea unui pret devine astfel imposibila structural,
nu doar improbabila prin prompt.

Descrierile spun *cand* se cheama tool-ul, nu doar ce face: pe modelele recente
conditia de declansare in descriere are efect masurabil asupra ratei de apel
corect, mai mare decat aceeasi instructiune mutata in promptul de sistem.

Doua tool-uri nu au ruta in spate, si asta e intentionat:

- `escalate_to_human` e o decizie de conversatie, nu de domeniu; ridica un steag
  pe care il citeste harness-ul (CLI, evals, si mai tarziu telefonia).
- cheia de idempotenta pentru `place_order` **nu** e parametru de tool. E derivata
  din `session_id`, ca un retry de retea sa nu poata produce a doua comanda —
  daca ar genera-o LLM-ul, ar genera alta la fiecare incercare.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from packages.domain.enums import (
    UPSELL_ORDER,
    EscalationReason,
    Fulfillment,
    PaymentMethod,
    SizeCode,
)

from .api_client import ApiResult, PizzaApiClient

_SIZE_CODES = [size.value for size in SizeCode]
_UPSELL_CATEGORIES = [category.value for category in UPSELL_ORDER]
_ESCALATION_REASONS = [reason.value for reason in EscalationReason]

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_menu",
        "description": (
            "Cauta in meniul real al restaurantului. Cheam-o inainte de a adauga orice "
            "produs si ori de cate ori clientul intreaba ce exista, ce contine un produs, "
            "ce alergeni are sau cat costa. Returneaza produse reale cu id, marimi, "
            "preturi formatate, ingrediente, alergeni si disponibilitate. Fara acest apel "
            "nu ai id-uri valide si nu poti sti preturile."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Ce a spus clientul, in cuvintele lui: 'picanta', 'ceva cu ciuperci', "
                        "'quattro stagioni'. Omite-l ca sa listezi o categorie intreaga."
                    ),
                },
                "category": {
                    "type": "string",
                    "enum": ["pizza", *_UPSELL_CATEGORIES],
                    "description": "Restrange cautarea la o categorie.",
                },
            },
        },
    },
    {
        "name": "add_item",
        "description": (
            "Adauga un produs in cos. Cheam-o dupa `search_menu`, cu un `product_id` real "
            "de acolo. Backend-ul recalculeaza tot cosul si returneaza totalul nou — "
            "foloseste acel total, nu aduna singur."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "string",
                    "description": "Id-ul exact returnat de `search_menu`.",
                },
                "qty": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Cantitatea. Implicit 1.",
                },
                "size_code": {
                    "type": "string",
                    "enum": _SIZE_CODES,
                    "description": (
                        "Obligatoriu pentru produsele care au marimi (pizza). Daca clientul "
                        "nu a spus marimea, intreab-o inainte sa adaugi."
                    ),
                },
                "removed_ingredients": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Ingrediente scoase la cererea clientului ('fara ceapa'), exact cum "
                        "apar in ingredientele produsului."
                    ),
                },
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "update_item",
        "description": (
            "Modifica o linie deja din cos: alta cantitate, alta marime, alte ingrediente "
            "scoase. Cheam-o cand clientul se razgandeste asupra a ceva ce a comandat deja, "
            "inclusiv dupa ce ai citit rezumatul. Trimite doar campurile care se schimba."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "line_id": {
                    "type": "string",
                    "description": "Id-ul liniei din cos, din ultimul cos returnat.",
                },
                "qty": {"type": "integer", "minimum": 1},
                "size_code": {"type": "string", "enum": _SIZE_CODES},
                "removed_ingredients": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["line_id"],
        },
    },
    {
        "name": "remove_item",
        "description": (
            "Scoate o linie din cos. Cheam-o cand clientul renunta la un produs. Pentru "
            "'nu mai vreau nimic' scoate liniile pe rand, apoi confirma anularea."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "line_id": {"type": "string", "description": "Id-ul liniei de scos."},
            },
            "required": ["line_id"],
        },
    },
    {
        "name": "set_fulfillment",
        "description": (
            "Stabileste livrare sau ridicare de la restaurant. Cheam-o devreme, imediat "
            "dupa produsele principale si inainte de upsell: taxa de livrare si comanda "
            "minima depind de ea, iar la livrare urmeaza verificarea adresei."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fulfillment": {
                    "type": "string",
                    "enum": [item.value for item in Fulfillment],
                },
            },
            "required": ["fulfillment"],
        },
    },
    {
        "name": "resolve_address",
        "description": (
            "Trimite adresa rostita de client, ca text brut, si primeste inapoi candidati "
            "plus verdictul de zona. Cheam-o imediat ce clientul spune adresa, inainte de "
            "orice upsell. Rezultatul poate fi: rezolvata, ambigua (cere lamurire), in "
            "afara zonei sau sub comanda minima pentru zona. Nu decide tu daca livram."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": (
                        "Adresa exact cum a rostit-o clientul, cu bloc, scara si apartament "
                        "daca le-a spus. Nu o normaliza si nu o completa."
                    ),
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "set_contact",
        "description": (
            "Salveaza telefonul si prenumele clientului. Telefonul se cere devreme (e "
            "singura cale de a-l suna daca ceva nu iese); prenumele la final, e suficient."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "Numarul, cifra cu cifra cum l-a dat."},
                "name": {"type": "string", "description": "Prenumele. Gol daca nu l-a spus inca."},
            },
            "required": ["phone"],
        },
    },
    {
        "name": "set_payment",
        "description": (
            "Stabileste plata: numerar sau card la livrare/ridicare, prin POS. Nu cere "
            "niciodata numarul cardului si nu accepta daca clientul incepe sa il dicteze."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "payment": {
                    "type": "string",
                    "enum": [item.value for item in PaymentMethod],
                },
            },
            "required": ["payment"],
        },
    },
    {
        "name": "mark_asked",
        "description": (
            "Marcheaza o categorie de upsell ca deja propusa. Cheam-o imediat ce ai "
            "propus-o, indiferent de raspuns — mai ales dupa un refuz. Asa categoria se "
            "inchide definitiv si nu o mai propui a doua oara."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": _UPSELL_CATEGORIES},
            },
            "required": ["category"],
        },
    },
    {
        "name": "get_order_summary",
        "description": (
            "Cere textul canonic al comenzii: produse, total si timp estimat. Cheam-o "
            "inainte de confirmarea finala si ori de cate ori clientul intreaba 'cat face' "
            "sau 'ce am comandat'. Citeste `spoken_text` ca atare — e singura formulare "
            "corecta a totalului si a ETA-ului."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "place_order",
        "description": (
            "Plaseaza comanda. Cheam-o doar dupa ce ai citit rezumatul si clientul a "
            "confirmat explicit. Backend-ul revalideaza tot de la zero si poate refuza "
            "(stoc, zona, comanda minima) — daca refuza, comanda nu exista. Apelul e "
            "idempotent: o reincercare dupa o eroare de retea nu produce comanda dubla."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "escalate_to_human",
        "description": (
            "Preda apelul unui coleg. Cheam-o la alergie, reclamatie, client nervos, "
            "comanda foarte mare, cerere in afara a ce poti face, sau cand ai inteles "
            "gresit acelasi lucru de doua ori la rand. Preferabil sa predai devreme decat "
            "sa duci la capat o comanda gresita."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "enum": _ESCALATION_REASONS},
                "note": {
                    "type": "string",
                    "description": "O propozitie pentru colegul care preia apelul.",
                },
            },
            "required": ["reason"],
        },
    },
]


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """Ce se trimite inapoi modelului pentru un `tool_use`."""

    content: str
    is_error: bool = False


@dataclass
class ToolContext:
    """Starea pe care o tine harness-ul, nu modelul."""

    client: PizzaApiClient
    session_id: str
    escalation: dict[str, str] | None = None
    placed_order_id: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def idempotency_key(self) -> str:
        """Stabila pe sesiune: acelasi apel, aceeasi cheie, cel mult o comanda."""
        return f"session-{self.session_id}"


def dispatch(context: ToolContext, name: str, arguments: dict[str, Any]) -> ToolOutcome:
    """Executa un tool si intoarce rezultatul serializat pentru model."""
    outcome = _dispatch(context, name, arguments)
    context.calls.append(
        {"tool": name, "input": arguments, "is_error": outcome.is_error, "output": outcome.content}
    )
    return outcome


def _dispatch(context: ToolContext, name: str, arguments: dict[str, Any]) -> ToolOutcome:
    client, sid = context.client, context.session_id

    if name == "escalate_to_human":
        context.escalation = {
            "reason": str(arguments.get("reason", EscalationReason.CUSTOMER_REQUEST.value)),
            "note": str(arguments.get("note", "")),
        }
        return ToolOutcome(
            _dump({"escalated": True, "instructiune": "Anunta clientul si asteapta preluarea."})
        )

    if name == "search_menu":
        return _wrap(client.search_menu(arguments.get("query"), arguments.get("category")))
    if name == "add_item":
        return _wrap(client.add_item(sid, _add_item_body(arguments)))
    if name == "update_item":
        line_id, body = _update_item_body(arguments)
        return _wrap(client.update_item(sid, line_id, body))
    if name == "remove_item":
        return _wrap(client.remove_item(sid, str(arguments["line_id"])))
    if name == "set_fulfillment":
        return _wrap(client.set_fulfillment(sid, str(arguments["fulfillment"])))
    if name == "resolve_address":
        return _wrap(client.resolve_address(sid, str(arguments["text"])))
    if name == "set_contact":
        return _wrap(
            client.set_contact(sid, str(arguments["phone"]), str(arguments.get("name", "")))
        )
    if name == "set_payment":
        return _wrap(client.set_payment(sid, str(arguments["payment"])))
    if name == "mark_asked":
        return _wrap(client.mark_asked(sid, str(arguments["category"])))
    if name == "get_order_summary":
        return _wrap(client.get_order_summary(sid))
    if name == "place_order":
        result = client.place_order(sid, context.idempotency_key)
        if result.ok and isinstance(result.data, dict):
            context.placed_order_id = str(result.data.get("id", "")) or None
        return _wrap(result)

    return ToolOutcome(
        _dump({"code": "unknown_tool", "message": f"Tool necunoscut: {name}."}), True
    )


def _add_item_body(arguments: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"product_id": str(arguments["product_id"])}
    if "qty" in arguments:
        body["qty"] = int(arguments["qty"])
    if arguments.get("size_code"):
        body["size_code"] = str(arguments["size_code"])
    if arguments.get("removed_ingredients"):
        body["removed_ingredients"] = [str(item) for item in arguments["removed_ingredients"]]
    return body


def _update_item_body(arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    body: dict[str, Any] = {}
    if "qty" in arguments:
        body["qty"] = int(arguments["qty"])
    if arguments.get("size_code"):
        body["size_code"] = str(arguments["size_code"])
    if arguments.get("removed_ingredients") is not None:
        body["removed_ingredients"] = [str(item) for item in arguments["removed_ingredients"]]
    return str(arguments["line_id"]), body


def _wrap(result: ApiResult) -> ToolOutcome:
    """Reusita -> payload-ul brut. Refuz -> acelasi payload structurat, marcat eroare.

    Mesajul refuzului ajunge la model exact cum l-a formulat domeniul, ca sa il
    poata reda fidel. Un refuz de domeniu nu opreste conversatia; o defectiune
    de transport ii spune modelului sa escaladeze, nu sa reincerce.
    """
    if result.ok:
        return ToolOutcome(_dump(result.data))
    payload: dict[str, Any] = {"code": result.code, "message": result.message}
    if result.field:
        payload["field"] = result.field
    if not result.is_domain_refusal:
        payload["hint"] = "Defectiune tehnica. Nu reincerca la nesfarsit — escaladeaza."
    return ToolOutcome(_dump(payload), True)


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)
