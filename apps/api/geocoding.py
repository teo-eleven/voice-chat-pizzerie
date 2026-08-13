"""Geocoding offline pentru Faza 1: fara cheie externa, rezolvare pe fixture local.

`load_fixture` face I/O (citeste `data/addresses.fixture.json`); `parse_spoken_address`
si `geocode` sunt pure, ca sa fie testabile fara disc. Decizia OK/AMBIGUOUS/
OUT_OF_ZONE/NOT_FOUND nu se ia aici — e a lui `delivery_zone.resolve_from_candidates`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from packages.domain import text
from packages.domain.models import Address, AddressCandidate

_DEFAULT_FIXTURE_PATH = Path("data/addresses.fixture.json")

#: Numarul e ultimul token numeric (cu litera optionala) din primul segment al adresei.
_STREET_NUMBER_RE = re.compile(r"^(?P<street>.+?)\s+(?P<number>\d+[a-zA-Z]?)\s*$")

#: Detalii de livrare, cautate oriunde in text, nu doar in primul segment.
_DETAIL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("block", re.compile(r"\bbloc(?:ul)?\s+([\w]+)", re.IGNORECASE)),
    ("staircase", re.compile(r"\bscar(?:a|ă)\s+([\w]+)", re.IGNORECASE)),
    ("floor", re.compile(r"\betaj(?:ul)?\s+([\w]+)", re.IGNORECASE)),
    ("apartment", re.compile(r"\b(?:apartament(?:ul)?|ap\.?)\s+([\w]+)", re.IGNORECASE)),
    ("intercom", re.compile(r"\binterfon(?:ul)?\s+([\w]+)", re.IGNORECASE)),
)


class AddressFixtureEntry(BaseModel):
    """O intrare din `data/addresses.fixture.json`."""

    model_config = ConfigDict(frozen=True)

    street: str
    number: str
    lat: float
    lon: float
    confidence: float
    city: str | None = None
    sector: str | None = None


def load_fixture(path: Path | str = _DEFAULT_FIXTURE_PATH) -> tuple[AddressFixtureEntry, ...]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return tuple(AddressFixtureEntry.model_validate(entry) for entry in raw)


def parse_spoken_address(spoken_text: str) -> dict[str, str]:
    """Extrage strada, numarul si detaliile de livrare dintr-un text rostit.

    Ex: „Strada Ștefan cel Mare 24, bloc 12, scara B, apartament 47” -> street/number/
    block/staircase/apartment. Detaliile absente nu apar in dict.
    """
    first_segment = spoken_text.split(",")[0].strip()
    match = _STREET_NUMBER_RE.match(first_segment)
    result = (
        {"street": match.group("street").strip(), "number": match.group("number").strip()}
        if match
        else {"street": first_segment, "number": ""}
    )
    for field, pattern in _DETAIL_PATTERNS:
        found = pattern.search(spoken_text)
        if found:
            result[field] = found.group(1)
    return result


def geocode(
    spoken_text: str, fixture: tuple[AddressFixtureEntry, ...]
) -> tuple[AddressCandidate, ...]:
    """Candidati din fixture a caror strada+numar se potrivesc cu textul rostit."""
    parsed = parse_spoken_address(spoken_text)
    street_key = text.normalize(parsed["street"])
    matches = tuple(
        entry
        for entry in fixture
        if text.normalize(entry.street) == street_key and entry.number == parsed["number"]
    )
    return tuple(_to_candidate(entry, parsed) for entry in matches)


def _to_candidate(entry: AddressFixtureEntry, parsed: dict[str, str]) -> AddressCandidate:
    address = Address(
        street=entry.street,
        number=entry.number,
        block=parsed.get("block"),
        staircase=parsed.get("staircase"),
        floor=parsed.get("floor"),
        apartment=parsed.get("apartment"),
        intercom=parsed.get("intercom"),
        lat=entry.lat,
        lon=entry.lon,
        formatted=_format_address(entry, parsed),
    )
    # `in_zone` e recalculat de `delivery_zone.resolve_from_candidates`; placeholder aici.
    return AddressCandidate(address=address, confidence=entry.confidence, in_zone=False)


def _format_address(entry: AddressFixtureEntry, parsed: dict[str, str]) -> str:
    parts = [f"{entry.street} {entry.number}"]
    labels = {
        "block": "bloc",
        "staircase": "scara",
        "floor": "etaj",
        "apartment": "apartament",
        "intercom": "interfon",
    }
    for field, label in labels.items():
        value = parsed.get(field)
        if value:
            parts.append(f"{label} {value}")
    return ", ".join(parts)
