"""Geocodare offline: fara cheie externa, rezolvare pe datele locale din `data/`.

`load_fixture` si `load_streets` fac I/O; `parse_spoken_address`, `AddressIndex` si
`geocode` sunt pure, ca sa fie testabile fara disc. Decizia OK/AMBIGUOUS/
OUT_OF_ZONE/NOT_FOUND nu se ia aici — e a lui `delivery_zone.resolve_from_candidates`.

Potrivirea se face pe chei, nu pe text brut: `address_text.street_key` face ca
„Ștefan cel Mare", „Ştefan cel Mare" si „Strada Ștefan cel Mare" sa cada pe aceeasi
strada, indiferent daca numele vine din OSM, din fixture sau din gura clientului.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from packages.domain.address_text import house_number_key, street_key
from packages.domain.limits import (
    MAX_HOUSE_NUMBER_LEN,
    MAX_POSTCODE_LEN,
    MAX_STREET_NAME_LEN,
)
from packages.domain.models import Address, AddressCandidate

_DEFAULT_FIXTURE_PATH = Path("data/addresses.fixture.json")
_DEFAULT_STREETS_PATH = Path("data/suceava.streets.json")

#: Localizarea implicita a datelor: un singur oras, un singur judet, o singura tara.
#: Tinuta ca implicit de model, nu repetata pe fiecare intrare din fisier — 3800 de
#: adrese ar purta de 3800 de ori acelasi „Suceava, Suceava, România".
DEFAULT_CITY = "Suceava"
DEFAULT_COUNTY = "Suceava"
DEFAULT_COUNTRY = "România"

#: Numarul e ultimul token numeric din primul segment al adresei, cu sufixul lui de
#: litere. Sufixul are pana la patru litere, cat accepta si `parse_house_number`:
#: Suceava chiar are „Marasesti 64SCA" si „Prunului 1BIS", iar un sufix de o singura
#: litera le-ar face negasibile prin voce.
_STREET_NUMBER_RE = re.compile(r"^(?P<street>.+?)\s+(?P<number>\d+\s?[a-zA-Z]{0,4})\s*$")

#: Detalii de livrare, cautate oriunde in text, nu doar in primul segment.
_DETAIL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("block", re.compile(r"\bbloc(?:ul)?\s+([\w]+)", re.IGNORECASE)),
    ("staircase", re.compile(r"\bscar(?:a|ă)\s+([\w]+)", re.IGNORECASE)),
    ("floor", re.compile(r"\betaj(?:ul)?\s+([\w]+)", re.IGNORECASE)),
    ("apartment", re.compile(r"\b(?:apartament(?:ul)?|ap\.?)\s+([\w]+)", re.IGNORECASE)),
    ("intercom", re.compile(r"\binterfon(?:ul)?\s+([\w]+)", re.IGNORECASE)),
)


class AddressFixtureEntry(BaseModel):
    """O intrare din `data/addresses.fixture.json`.

    Lungimile sunt plafonate fiindca datele vin din OpenStreetMap, unde textul e
    scris de oricine: fara plafon, o singura eticheta absurda ar intra in fisier,
    de acolo in memoria serverului la fiecare pornire si mai departe in raspunsuri.
    """

    model_config = ConfigDict(frozen=True)

    street: str = Field(max_length=MAX_STREET_NAME_LEN)
    number: str = Field(max_length=MAX_HOUSE_NUMBER_LEN)
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    confidence: float = Field(ge=0.0, le=1.0)
    city: str = Field(default=DEFAULT_CITY, max_length=MAX_STREET_NAME_LEN)
    county: str = Field(default=DEFAULT_COUNTY, max_length=MAX_STREET_NAME_LEN)
    country: str = Field(default=DEFAULT_COUNTRY, max_length=MAX_STREET_NAME_LEN)
    postcode: str | None = Field(default=None, max_length=MAX_POSTCODE_LEN)
    sector: str | None = Field(default=None, max_length=MAX_STREET_NAME_LEN)
    #: De unde vine adresa. Importul rescrie tot ce e `osm` si pastreaza tot ce e
    #: `manual`, deci fara campul asta o re-rulare n-ar mai putea sterge nimic: o
    #: adresa disparuta din OpenStreetMap ar ramane in fisier pe veci. Implicitul e
    #: `manual` fiindca a pierde o adresa scrisa de om e mai grav decat a pastra una
    #: in plus — o intrare fara camp e tratata ca scrisa de om.
    source: Literal["osm", "manual"] = "manual"


class StreetEntry(BaseModel):
    """O strada din `data/suceava.streets.json`.

    `lat`/`lon` e un punct reprezentativ (mijlocul segmentelor), folosit ca sa se
    poata verifica dintr-o privire daca strada cade in zona de livrare; nu e o
    adresa si nu se livreaza la el.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(max_length=MAX_STREET_NAME_LEN)
    key: str = Field(max_length=MAX_STREET_NAME_LEN)
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    address_count: int = Field(default=0, ge=0)


class StreetRegistry(BaseModel):
    """Registrul de strazi al municipiului, asa cum e scris pe disc."""

    model_config = ConfigDict(frozen=True)

    city: str = Field(default=DEFAULT_CITY, max_length=MAX_STREET_NAME_LEN)
    county: str = Field(default=DEFAULT_COUNTY, max_length=MAX_STREET_NAME_LEN)
    country: str = Field(default=DEFAULT_COUNTRY, max_length=MAX_STREET_NAME_LEN)
    streets: tuple[StreetEntry, ...] = ()


#: `eq=False`: fara el, `frozen=True` genereaza un `__hash__` care crapa la prima
#: folosire, fiindca dictionarul din interior nu e hashabil. Indexul nu se compara
#: si nu se pune in seturi — e o structura de cautare, nu o valoare.
@dataclass(frozen=True, slots=True, eq=False)
class AddressIndex:
    """Fixture-ul pregatit pentru cautare, construit o data la pornirea API-ului.

    Fara el, fiecare adresa rostita ar normaliza pe loc numele tuturor celor ~3800
    de intrari din fixture. Cheia e (strada, numar), amandoua normalizate.
    """

    entries: tuple[AddressFixtureEntry, ...]
    by_street_and_number: Mapping[tuple[str, str], tuple[AddressFixtureEntry, ...]]

    @classmethod
    def build(cls, entries: tuple[AddressFixtureEntry, ...]) -> AddressIndex:
        grouped: dict[tuple[str, str], list[AddressFixtureEntry]] = defaultdict(list)
        for entry in entries:
            grouped[(street_key(entry.street), house_number_key(entry.number))].append(entry)
        return cls(
            entries=entries,
            by_street_and_number={key: tuple(value) for key, value in grouped.items()},
        )

    def lookup(self, street: str, number: str) -> tuple[AddressFixtureEntry, ...]:
        return self.by_street_and_number.get((street_key(street), house_number_key(number)), ())


def load_fixture(path: Path | str = _DEFAULT_FIXTURE_PATH) -> tuple[AddressFixtureEntry, ...]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return tuple(AddressFixtureEntry.model_validate(entry) for entry in raw)


def load_streets(path: Path | str = _DEFAULT_STREETS_PATH) -> StreetRegistry:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return StreetRegistry.model_validate(raw)


def load_index(path: Path | str = _DEFAULT_FIXTURE_PATH) -> AddressIndex:
    return AddressIndex.build(load_fixture(path))


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


def geocode(spoken_text: str, index: AddressIndex) -> tuple[AddressCandidate, ...]:
    """Candidatii din fixture a caror strada+numar se potrivesc cu textul rostit."""
    parsed = parse_spoken_address(spoken_text)
    if not parsed["number"]:
        return ()
    matches = index.lookup(parsed["street"], parsed["number"])
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
