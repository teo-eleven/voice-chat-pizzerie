"""CLI pentru administrarea fixture-ului de adrese folosit de geocodarea offline.

Proprietarul pizzeriei poate adăuga, lista sau verifica adrese fără să editeze
`data/addresses.fixture.json` de mână. Reutilizează modelul `AddressFixtureEntry`
din `apps/api/geocoding.py` și verificarea de poligon din
`packages/domain/delivery_zone.py`, ca să nu existe două surse de adevăr pentru
formatul unei adrese sau pentru „ce înseamnă în zonă”.

Exemple:
    uv run python scripts/add_address.py add --street "Strada Zorilor" --number 12 \
        --lat 47.6600 --lon 26.2650
    uv run python scripts/add_address.py list
    uv run python scripts/add_address.py check --lat 47.66 --lon 26.265
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

# rulează de oriunde: rădăcina proiectului e părintele lui scripts/
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # fixture-ul și zona se încarcă din căi relative la rădăcină

from pydantic import ValidationError  # noqa: E402

from apps.api.geocoding import AddressFixtureEntry, load_fixture  # noqa: E402
from packages.domain import config, delivery_zone, text  # noqa: E402
from packages.domain.errors import DomainError  # noqa: E402
from packages.domain.models import ZoneConfig  # noqa: E402

FIXTURE_PATH = ROOT / "data" / "addresses.fixture.json"
ZONE_PATH = ROOT / "data" / "delivery_zone.json"

#: Limite geografice valide (WGS84); orice în afara lor e o coordonată imposibilă.
LATITUDE_MIN = -90.0
LATITUDE_MAX = 90.0
LONGITUDE_MIN = -180.0
LONGITUDE_MAX = 180.0

#: Încredere implicită pentru o adresă introdusă manual de proprietar.
DEFAULT_CONFIDENCE = 0.95
DEFAULT_CITY = "Suceava"

#: Permisiunile fixture-ului, aliniate cu restul fișierelor din `data/`.
_FIXTURE_FILE_MODE = 0o644


class AddressScriptError(Exception):
    """Eroare de utilizator, gata de afișat direct — fără traceback."""


def build_parser() -> argparse.ArgumentParser:
    """Construiește parserul cu cele trei subcomenzi: `add`, `list`, `check`."""
    parser = argparse.ArgumentParser(
        prog="add_address.py",
        description="Administrează adresele din fixture-ul de geocodare offline.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Adaugă o adresă nouă în fixture.")
    add_parser.add_argument("--street", required=True, help="Numele străzii, ex: „Strada Zorilor”.")
    add_parser.add_argument("--number", required=True, help="Numărul, ex: „12” sau „12A”.")
    add_parser.add_argument("--lat", required=True, type=float, help="Latitudine (WGS84).")
    add_parser.add_argument("--lon", required=True, type=float, help="Longitudine (WGS84).")
    add_parser.add_argument(
        "--confidence",
        type=float,
        default=DEFAULT_CONFIDENCE,
        help=f"Încredere geocodare, 0-1 (implicit {DEFAULT_CONFIDENCE}).",
    )
    add_parser.add_argument(
        "--city", default=DEFAULT_CITY, help=f"Oraș (implicit „{DEFAULT_CITY}”)."
    )

    subparsers.add_parser("list", help="Afișează toate adresele din fixture.")

    check_parser = subparsers.add_parser(
        "check", help="Verifică dacă un punct e în zona de livrare, fără să scrie nimic."
    )
    check_parser.add_argument("--lat", required=True, type=float, help="Latitudine (WGS84).")
    check_parser.add_argument("--lon", required=True, type=float, help="Longitudine (WGS84).")

    return parser


def _validate_coordinates(lat: float, lon: float) -> None:
    """Refuză coordonate imposibile înainte să ajungă în fixture sau în poligon."""
    if not (LATITUDE_MIN <= lat <= LATITUDE_MAX):
        raise AddressScriptError(
            f"Latitudinea {lat} este în afara intervalului valid "
            f"[{LATITUDE_MIN}, {LATITUDE_MAX}]."
        )
    if not (LONGITUDE_MIN <= lon <= LONGITUDE_MAX):
        raise AddressScriptError(
            f"Longitudinea {lon} este în afara intervalului valid "
            f"[{LONGITUDE_MIN}, {LONGITUDE_MAX}]."
        )


def _load_fixture_safe(path: Path) -> tuple[AddressFixtureEntry, ...]:
    """`load_fixture`, cu erorile traduse în mesaje utile în română."""
    try:
        return load_fixture(path)
    except FileNotFoundError as exc:
        raise AddressScriptError(f"Fișierul de adrese „{path}” nu a fost găsit.") from exc
    except json.JSONDecodeError as exc:
        raise AddressScriptError(
            f"Fișierul de adrese „{path}” conține JSON invalid: {exc}"
        ) from exc
    except ValidationError as exc:
        raise AddressScriptError(
            f"Fișierul de adrese „{path}” conține date invalide:\n{exc}"
        ) from exc


def _load_zone_safe(path: Path) -> ZoneConfig:
    """`config.load_zone_config`, cu erorile traduse în mesaje utile în română."""
    try:
        return config.load_zone_config(path)
    except FileNotFoundError as exc:
        raise AddressScriptError(f"Fișierul zonei de livrare „{path}” nu a fost găsit.") from exc
    except json.JSONDecodeError as exc:
        raise AddressScriptError(
            f"Fișierul zonei de livrare „{path}” conține JSON invalid: {exc}"
        ) from exc
    except (ValidationError, ValueError) as exc:
        raise AddressScriptError(
            f"Fișierul zonei de livrare „{path}” conține date invalide:\n{exc}"
        ) from exc


def _is_same_address(
    entry: AddressFixtureEntry, street_key: str, number: str, lat: float, lon: float
) -> bool:
    """Aceeași stradă normalizată + același număr + aceleași coordonate exacte."""
    return (
        text.normalize(entry.street) == street_key
        and entry.number == number
        and entry.lat == lat
        and entry.lon == lon
    )


def _write_fixture_atomic(path: Path, entries: tuple[AddressFixtureEntry, ...]) -> None:
    """Scrie fixture-ul într-un fișier temporar și îl mută peste original.

    `mkstemp` creează fișierul atomic și întoarce un descriptor deja deschis; o
    întrerupere la mijloc lasă temporarul pe disc, nu fixture-ul corupt.

    Permisiunile se pun explicit pe `0644`: `mkstemp` creează cu `0600`, iar `replace`
    păstrează modul temporarului. Fără linia asta, fiecare rulare a scriptului ar
    strânge tăcut drepturile fixture-ului față de restul fișierelor din `data/`, iar
    serverul rulat sub alt utilizator ar ajunge să nu-și mai poată citi propriile date.
    """
    payload = [entry.model_dump(exclude_none=True) for entry in entries]
    content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(content)
        tmp_path.chmod(_FIXTURE_FILE_MODE)
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _run_add(
    street: str, number: str, lat: float, lon: float, confidence: float, city: str
) -> int:
    """Adaugă o adresă în fixture, după validare, verificare de zonă și duplicate."""
    _validate_coordinates(lat, lon)

    entries = _load_fixture_safe(FIXTURE_PATH)
    zone = _load_zone_safe(ZONE_PATH)

    street_key = text.normalize(street)
    duplicate = next(
        (entry for entry in entries if _is_same_address(entry, street_key, number, lat, lon)),
        None,
    )
    if duplicate is not None:
        raise AddressScriptError(
            f"Adresa „{street} {number}” cu coordonatele ({lat}, {lon}) există deja "
            "în fixture — nu o adaug din nou."
        )

    try:
        new_entry = AddressFixtureEntry(
            street=street, number=number, lat=lat, lon=lon, confidence=confidence, city=city
        )
    except ValidationError as exc:
        raise AddressScriptError(f"Datele adresei sunt invalide:\n{exc}") from exc

    in_zone = delivery_zone.point_in_polygon(new_entry.lat, new_entry.lon, zone.polygon)
    _write_fixture_atomic(FIXTURE_PATH, (*entries, new_entry))

    print(f"Adresa „{new_entry.street} {new_entry.number}” a fost adăugată în fixture.")
    if in_zone:
        print("Verificare zonă: ÎN ZONA de livrare.")
    else:
        print(
            "Verificare zonă: ÎN AFARA zonei de livrare. ATENȚIE: la o comandă reală, "
            "adresa asta va fi respinsă cu mesajul „nu livrăm în zona aceasta”. "
            "Am adăugat-o oricum — utilă pentru testarea refuzului, dar confirmați "
            "că asta ați vrut."
        )
    return 0


def _run_list() -> int:
    """Afișează toate adresele din fixture, marcate ÎN ZONĂ / ÎN AFARA ZONEI."""
    entries = _load_fixture_safe(FIXTURE_PATH)
    zone = _load_zone_safe(ZONE_PATH)

    if not entries:
        print("Fixture-ul de adrese este gol.")
        return 0

    in_zone_count = 0
    for entry in entries:
        in_zone = delivery_zone.point_in_polygon(entry.lat, entry.lon, zone.polygon)
        in_zone_count += int(in_zone)
        status = "ÎN ZONĂ" if in_zone else "ÎN AFARA ZONEI"
        city = f", {entry.city}" if entry.city else ""
        print(f"{entry.street} {entry.number}{city} ({entry.lat}, {entry.lon}) — {status}")

    out_of_zone_count = len(entries) - in_zone_count
    print(
        f"\nTotal: {len(entries)} adrese — {in_zone_count} în zonă, "
        f"{out_of_zone_count} în afara zonei."
    )
    return 0


def _run_check(lat: float, lon: float) -> int:
    """Spune doar dacă punctul e în zonă; nu scrie nimic pe disc."""
    _validate_coordinates(lat, lon)
    zone = _load_zone_safe(ZONE_PATH)
    in_zone = delivery_zone.point_in_polygon(lat, lon, zone.polygon)
    verdict = "ÎN zona de livrare." if in_zone else "ÎN AFARA zonei de livrare."
    print(f"Punctul ({lat}, {lon}) este {verdict}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Punct de intrare CLI: parsează argumentele și dispecerizează comanda."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        match args.command:
            case "add":
                return _run_add(
                    street=args.street,
                    number=args.number,
                    lat=args.lat,
                    lon=args.lon,
                    confidence=args.confidence,
                    city=args.city,
                )
            case "list":
                return _run_list()
            case "check":
                return _run_check(lat=args.lat, lon=args.lon)
            case _:
                raise AssertionError(f"Comandă necunoscută: {args.command!r}")
    except AddressScriptError as exc:
        print(f"Eroare: {exc}", file=sys.stderr)
        return 1
    except DomainError as exc:
        # `point_in_polygon` refuză un poligon cu mai puțin de trei puncte ridicând
        # `DomainError`, care nu descinde din `AddressScriptError`. Fără ramura asta,
        # un `data/delivery_zone.json` stricat producea un traceback brut — singurul
        # loc din script care nu ieșea cu mesaj omenesc.
        print(f"Eroare: {exc.issue.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
