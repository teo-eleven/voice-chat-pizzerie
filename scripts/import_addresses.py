"""Import de străzi și numere pentru municipiul Suceava, din OpenStreetMap.

Sursa e Overpass API, interogat pe relația administrativă a municipiului
(`admin_level=8`), nu pe un dreptunghi de coordonate: un dreptunghi ar prinde și
comunele vecine, iar adresele lor n-au ce căuta în datele pizzeriei.

Rezultatul sunt două fișiere din `data/`:

- `suceava.streets.json` — registrul de străzi, cu un punct reprezentativ;
- `addresses.fixture.json` — adresele (stradă + număr + coordonate), pe care le
  caută geocodarea offline.

Datele OSM sunt scrise de oameni, deci sunt inconsecvente: aceeași stradă apare
și „Ştefan cel Mare", și „Strada Ștefan cel Mare"; numerele apar și „12A", și
„nr 140", și „F.N.". Curățarea e în `packages/domain/address_text.py`, ca să fie
testabilă fără rețea, iar aici rămâne doar orchestrarea.

Licență: datele OSM sunt sub ODbL — atribuirea către OpenStreetMap se păstrează
în antetul fișierelor generate.

Exemple:
    uv run python scripts/import_addresses.py fetch          # descarcă în cache
    uv run python scripts/import_addresses.py build          # scrie data/ din cache
    uv run python scripts/import_addresses.py build --dry-run
    uv run python scripts/import_addresses.py verify         # validează ce e pe disc
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

# rulează de oriunde: rădăcina proiectului e părintele lui scripts/
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # datele se scriu și se citesc din căi relative la rădăcină

import httpx  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from apps.api.geocoding import (  # noqa: E402
    DEFAULT_CITY,
    DEFAULT_COUNTRY,
    DEFAULT_COUNTY,
    AddressFixtureEntry,
    StreetEntry,
    StreetRegistry,
    load_fixture,
    load_streets,
)
from packages.domain import config, delivery_zone  # noqa: E402
from packages.domain.address_text import (  # noqa: E402
    canonical_street,
    house_number_key,
    parse_house_number,
    preferred_street_name,
    street_key,
)
from packages.fileio import write_json_atomic  # noqa: E402

FIXTURE_PATH = ROOT / "data" / "addresses.fixture.json"
STREETS_PATH = ROOT / "data" / "suceava.streets.json"
ZONE_PATH = ROOT / "data" / "delivery_zone.json"
CACHE_DIR = ROOT / "data" / "osm-cache"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

#: Relația OSM a municipiului Suceava (`boundary=administrative`, `admin_level=8`).
#: Overpass cere id-ul de arie, care e id-ul relației plus 3.600.000.000.
SUCEAVA_RELATION_ID = 10495794
OSM_AREA_OFFSET = 3_600_000_000

#: Overpass e un serviciu public gratuit: o interogare pe tot municipiul durează
#: zeci de secunde și poate fi pusă la coadă.
OVERPASS_TIMEOUT_SECONDS = 300
OVERPASS_QUERY_TIMEOUT_SECONDS = 280

#: Încrederea unei adrese importate. Sub cea a unei adrese introduse manual
#: (0.95): OSM e bun, dar nu verificat de proprietar. Un punct pe clădire e mai
#: sigur decât centrul unei clădiri desenate ca poligon.
CONFIDENCE_NODE = 0.90
CONFIDENCE_AREA = 0.85

#: Localitățile acceptate: doar municipiul. Aria administrativă atinge și sate
#: vecine (Sfântu Ilie, comuna Șcheia), care nu fac parte din municipiul Suceava.
ACCEPTED_CITIES = frozenset({DEFAULT_CITY})

_ADDRESS_QUERY = """[out:json][timeout:{timeout}];
area({area_id})->.suceava;
(
  node(area.suceava)["addr:housenumber"];
  way(area.suceava)["addr:housenumber"];
  relation(area.suceava)["addr:housenumber"];
);
out center tags;"""

_STREETS_QUERY = """[out:json][timeout:{timeout}];
area({area_id})->.suceava;
way(area.suceava)["highway"]["name"];
out center tags;"""

#: Valori de `highway` care sunt drumuri, dar nu străzi cu adrese: nu intră în
#: registru, ca livratorul să nu primească „Strada Trotuar" ca stradă validă.
_NON_STREET_HIGHWAYS = frozenset(
    {"footway", "path", "steps", "cycleway", "track", "pedestrian", "construction", "proposed"}
)


class ImportScriptError(Exception):
    """Eroare de utilizator, gata de afișat direct — fără traceback."""


# --------------------------------------------------------------------- descărcare


def _fetch(query: str) -> dict[str, Any]:
    """Trimite o interogare la Overpass și întoarce JSON-ul, cu erori traduse."""
    try:
        response = httpx.post(
            OVERPASS_URL,
            data={"data": query},
            timeout=OVERPASS_TIMEOUT_SECONDS,
            headers={"User-Agent": "pizza-punto-import/1.0 (import adrese Suceava)"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ImportScriptError(
                "Overpass a răspuns cu JSON care nu e un obiect — probabil un mesaj "
                "de eroare al serviciului, nu date."
            )
        return payload
    except httpx.HTTPStatusError as exc:
        raise ImportScriptError(
            f"Overpass a răspuns cu {exc.response.status_code}. "
            "Serviciul e gratuit și limitează traficul — încearcă din nou peste "
            "câteva minute."
        ) from exc
    except httpx.HTTPError as exc:
        raise ImportScriptError(f"Nu am putut contacta Overpass: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ImportScriptError(f"Overpass a răspuns cu ceva care nu e JSON: {exc}") from exc


def _run_fetch(area_id: int) -> int:
    """Descarcă adresele și străzile în `data/osm-cache/`, fără să atingă `data/`."""
    # Modul e explicit, ca directorul de cache să nu ajungă scriibil de tot grupul
    # pe o mașină cu umask permisiv.
    CACHE_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)
    for name, query in (
        ("addresses", _ADDRESS_QUERY),
        ("streets", _STREETS_QUERY),
    ):
        print(f"Descarc {name} din Overpass (poate dura un minut)...")
        payload = _fetch(
            query.format(timeout=OVERPASS_QUERY_TIMEOUT_SECONDS, area_id=area_id)
        )
        elements = payload.get("elements", [])
        write_json_atomic(CACHE_DIR / f"{name}.json", payload)
        print(f"  {len(elements)} elemente -> data/osm-cache/{name}.json")
    return 0


def _short(path: Path) -> str:
    """Calea scurtă, relativă la rădăcina proiectului, când e din proiect.

    În teste căile stau în `tmp_path`, deci `relative_to` ar ridica `ValueError` —
    un mesaj de eroare nu are voie să eșueze el însuși.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _read_cache(name: str) -> list[dict[str, Any]]:
    """Elementele OSM dintr-un fișier din cache."""
    path = CACHE_DIR / f"{name}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ImportScriptError(
            f"Lipsește „{_short(path)}”. Rulează întâi "
            "`uv run python scripts/import_addresses.py fetch`."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ImportScriptError(f"Fișierul „{_short(path)}” conține JSON invalid: {exc}") from exc
    elements = payload.get("elements")
    if not isinstance(elements, list):
        raise ImportScriptError(f"Fișierul „{_short(path)}” nu are lista „elements”.")
    return [element for element in elements if isinstance(element, dict)]


# ------------------------------------------------------------------ transformare


def _coordinates(element: dict[str, Any]) -> tuple[float, float] | None:
    """Coordonatele elementului: nodul are lat/lon, way-ul și relația au `center`."""
    source = element.get("center") if element.get("type") != "node" else element
    if not isinstance(source, dict):
        return None
    lat, lon = source.get("lat"), source.get("lon")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    return float(lat), float(lon)


def _tags(element: dict[str, Any]) -> dict[str, str]:
    """Etichetele elementului, păstrând doar perechile text-text.

    Overpass e o sursă externă: un `"tags": null` sau o valoare numerică ar opri
    importul cu `AttributeError` la mijlocul transformării. Ce nu e text se ignoră,
    ca o singură intrare stricată din OSM să nu blocheze celelalte câteva mii.
    """
    tags = element.get("tags")
    if not isinstance(tags, dict):
        return {}
    return {
        key: value
        for key, value in tags.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _display_names(
    address_elements: Iterable[dict[str, Any]], street_elements: Iterable[dict[str, Any]]
) -> dict[str, str]:
    """Numele canonic al fiecărei străzi, ales dintre toate scrierile ei întâlnite.

    Adună din ambele surse: numele drumurilor sunt de obicei scrise complet
    („Strada Pictor Panaitescu”), iar `addr:street` de pe adrese e adesea prescurtat
    („Panaitescu”) — împreună dau varianta cea mai bună pentru fiecare stradă.
    Calculat o singură dată, ca adresele și registrul de străzi să nu poată ajunge
    la nume diferite pentru aceeași cheie.
    """
    variants: dict[str, list[str]] = defaultdict(list)
    for element in street_elements:
        tags = _tags(element)
        name = tags.get("name")
        if name and tags.get("highway") not in _NON_STREET_HIGHWAYS:
            variants[street_key(name)].append(name)
    for element in address_elements:
        name = _tags(element).get("addr:street")
        if name:
            variants[street_key(name)].append(name)
    return {key: preferred_street_name(names) for key, names in variants.items()}


def _build_addresses(
    elements: Iterable[dict[str, Any]], names: dict[str, str]
) -> tuple[tuple[AddressFixtureEntry, ...], dict[str, int]]:
    """Adresele curate, deduplicate, plus contorii de intrări refuzate."""
    stats: dict[str, int] = defaultdict(int)
    seen: dict[tuple[str, str], AddressFixtureEntry] = {}

    for element in elements:
        tags = _tags(element)
        raw_street = tags.get("addr:street")
        raw_number = tags.get("addr:housenumber", "")
        if not raw_street:
            stats["fără stradă"] += 1
            continue
        # Apartenența la municipiu e deja asigurată geometric: interogarea Overpass
        # rulează pe `area(...)`, adică pe conturul administrativ al Suceviei, deci
        # o adresă fără `addr:city` e înăuntru, nu necunoscută. Verificarea de aici
        # prinde doar eticheta care se contrazice cu conturul (sate vecine mapate
        # peste graniță, ex. Sfântu Ilie din comuna Șcheia).
        city = tags.get("addr:city")
        if city is not None and city not in ACCEPTED_CITIES:
            stats["altă localitate"] += 1
            continue
        number = parse_house_number(raw_number)
        if number is None:
            stats["număr nefolosibil"] += 1
            continue
        point = _coordinates(element)
        if point is None:
            stats["fără coordonate"] += 1
            continue

        key = street_key(raw_street)
        try:
            entry = AddressFixtureEntry(
                street=names.get(key) or canonical_street(raw_street),
                number=number,
                lat=round(point[0], 7),
                lon=round(point[1], 7),
                confidence=CONFIDENCE_NODE if element.get("type") == "node" else CONFIDENCE_AREA,
                postcode=tags.get("addr:postcode"),
                source="osm",
            )
        except ValidationError:
            # Plafoanele de lungime și limitele de coordonate din model sunt ultima
            # barieră în fața a ce scrie un cartograf într-o etichetă. O intrare care
            # le încalcă se numără și se sare, ca restul celor câteva mii să intre —
            # la fel ca orice alt refuz din bucla asta.
            stats["dată invalidă"] += 1
            continue
        identity = (key, house_number_key(number))
        previous = seen.get(identity)
        if previous is None:
            seen[identity] = entry
            continue
        # Același număr apare de mai multe ori (clădirea și intrarea ei, sau două
        # scrieri ale străzii). Păstrăm o singură adresă — două candidaturi pe
        # același număr ar face agentul să ceară o clarificare imposibilă.
        stats["duplicat"] += 1
        seen[identity] = _better_duplicate(previous, entry)

    return tuple(sorted(seen.values(), key=_sort_key)), dict(stats)


def _better_duplicate(
    first: AddressFixtureEntry, second: AddressFixtureEntry
) -> AddressFixtureEntry:
    """Dintre două adrese cu aceeași identitate, o ține pe cea mai informativă.

    Coordonatele intră în criteriu ca departajare finală, nu fiindcă ar conta care
    e mai la nord: la scoruri egale, alegerea ar cădea altfel pe ordinea în care a
    răspuns Overpass, iar fiecare re-import ar produce un diff fără nicio schimbare
    reală în date.
    """
    return max(
        (first, second),
        key=lambda entry: (entry.confidence, entry.postcode is not None, entry.lat, entry.lon),
    )


def _sort_key(entry: AddressFixtureEntry) -> tuple[str, int, str]:
    """Ordonare stabilă: stradă, apoi număr crescător numeric, apoi sufix.

    Fără partea numerică, „10" ar veni înaintea lui „9" și fiecare re-import ar
    produce un diff greu de citit.
    """
    digits = "".join(char for char in entry.number if char.isdigit())
    suffix = entry.number[len(digits) :]
    return street_key(entry.street), int(digits or 0), suffix


def _build_streets(
    street_elements: Iterable[dict[str, Any]],
    names: dict[str, str],
    addresses: tuple[AddressFixtureEntry, ...],
) -> tuple[StreetEntry, ...]:
    """Registrul de străzi, cu punctul reprezentativ = media segmentelor.

    Străzile fără niciun segment cu coordonate (apar doar ca `addr:street` pe o
    adresă) primesc media adreselor lor, ca fiecare stradă din registru să aibă
    un punct.
    """
    points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for element in street_elements:
        tags = _tags(element)
        name = tags.get("name")
        if not name or tags.get("highway") in _NON_STREET_HIGHWAYS:
            continue
        point = _coordinates(element)
        if point is not None:
            points[street_key(name)].append(point)

    address_counts: dict[str, int] = defaultdict(int)
    address_points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    # Numele scris pe adresă e deja canonic; acoperă străzile manuale, pe care OSM
    # nu le cunoaște și care altfel ar lipsi din registru.
    address_names: dict[str, str] = {}
    for entry in addresses:
        key = street_key(entry.street)
        address_counts[key] += 1
        address_points[key].append((entry.lat, entry.lon))
        address_names.setdefault(key, entry.street)

    streets: list[StreetEntry] = []
    for key in sorted(set(points) | set(address_points)):
        located = points.get(key) or address_points.get(key) or []
        display = names.get(key) or address_names.get(key)
        # Fără nume canonic n-avem ce scrie în registru: `key` e normalizat
        # (minuscule, fără diacritice), iar o stradă scrisă „strada zorilor" pe
        # bonul livratorului arată a bug, nu a stradă.
        if not located or display is None:
            continue
        try:
            streets.append(
                StreetEntry(
                    name=display,
                    key=key,
                    lat=round(sum(lat for lat, _ in located) / len(located), 7),
                    lon=round(sum(lon for _, lon in located) / len(located), 7),
                    address_count=address_counts.get(key, 0),
                )
            )
        except ValidationError:
            # Ca la adrese: o stradă cu nume absurd de lung nu oprește registrul.
            continue
    return tuple(streets)


def _merge_curated(
    imported: tuple[AddressFixtureEntry, ...], curated: tuple[AddressFixtureEntry, ...]
) -> tuple[AddressFixtureEntry, ...]:
    """Păstrează adresele scrise manual pe care importul nu le acoperă.

    OSM nu are toate numerele (lipsește, de exemplu, Ștefan cel Mare 24, folosit de
    harness-ul de test). O adresă manuală care s-ar bate cu una importată se lasă
    deoparte: datele reale au prioritate, iar două intrări pe același număr ar
    produce o ambiguitate falsă.

    Se păstrează **doar** intrările marcate `manual`. Ce a venit din OSM la un import
    anterior se aruncă și se ia din nou din sursă — altfel o adresă ștearsă între timp
    din OpenStreetMap ar rămâne în fișier la nesfârșit, fără ca cineva s-o poată scoate.
    """
    imported_ids = {(street_key(e.street), house_number_key(e.number)) for e in imported}
    kept = tuple(
        entry
        for entry in curated
        if entry.source == "manual"
        and (street_key(entry.street), house_number_key(entry.number)) not in imported_ids
    )
    return tuple(sorted((*imported, *kept), key=_sort_key))


# ------------------------------------------------------------------------ comenzi


def _run_build(dry_run: bool) -> int:
    """Transformă cache-ul OSM în cele două fișiere din `data/`."""
    address_elements = _read_cache("addresses")
    street_elements = _read_cache("streets")

    names = _display_names(address_elements, street_elements)
    imported, stats = _build_addresses(address_elements, names)
    curated = _load_curated()
    addresses = _merge_curated(imported, curated)
    streets = _build_streets(street_elements, names, addresses)

    _print_report(address_elements, imported, curated, addresses, streets, stats)

    if dry_run:
        print("\n--dry-run: nu am scris nimic.")
        return 0

    write_json_atomic(FIXTURE_PATH, [e.model_dump(exclude_defaults=True) for e in addresses])
    registry = StreetRegistry(
        city=DEFAULT_CITY, county=DEFAULT_COUNTY, country=DEFAULT_COUNTRY, streets=streets
    )
    write_json_atomic(STREETS_PATH, _streets_payload(registry))
    print(f"\nScris: data/addresses.fixture.json ({len(addresses)} adrese)")
    print(f"Scris: data/suceava.streets.json ({len(streets)} străzi)")
    return 0


def _streets_payload(registry: StreetRegistry) -> dict[str, Any]:
    """Registrul, cu antetul de atribuire cerut de licența ODbL a datelor OSM."""
    return {
        "_comment": (
            "Registrul de străzi al municipiului Suceava, generat de "
            "scripts/import_addresses.py din date © colaboratorii OpenStreetMap (ODbL). "
            "Nu se editează manual — se regenerează."
        ),
        **registry.model_dump(),
    }


def _load_curated() -> tuple[AddressFixtureEntry, ...]:
    """Fixture-ul existent, dacă există; un import pe gol nu e o eroare."""
    if not FIXTURE_PATH.exists():
        return ()
    try:
        return load_fixture(FIXTURE_PATH)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ImportScriptError(
            f"Fixture-ul existent „{FIXTURE_PATH.name}” nu poate fi citit, deci nu pot "
            f"ști ce adrese manuale să păstrez:\n{exc}"
        ) from exc


def _print_report(
    raw_elements: list[dict[str, Any]],
    imported: tuple[AddressFixtureEntry, ...],
    curated: tuple[AddressFixtureEntry, ...],
    addresses: tuple[AddressFixtureEntry, ...],
    streets: tuple[StreetEntry, ...],
    stats: dict[str, int],
) -> None:
    """Ce a intrat, ce a fost refuzat și cât cade în zona de livrare."""
    print(f"Elemente OSM cu număr:        {len(raw_elements)}")
    for reason, count in sorted(stats.items()):
        print(f"  refuzate — {reason:<18} {count}")
    manual = sum(1 for entry in curated if entry.source == "manual")
    print(f"Adrese importate:             {len(imported)}")
    print(f"Adrese manuale păstrate:      {len(addresses) - len(imported)} din {manual}")
    print(f"Total în fixture:             {len(addresses)}")
    print(f"Străzi în registru:           {len(streets)}")

    zone = config.load_zone_config(ZONE_PATH)
    in_zone = sum(
        1 for e in addresses if delivery_zone.point_in_polygon(e.lat, e.lon, zone.polygon)
    )
    print(
        f"În zona de livrare curentă:   {in_zone} din {len(addresses)} "
        f"({in_zone * 100 // max(len(addresses), 1)}%)"
    )


def _run_verify() -> int:
    """Recitește ce e pe disc cu modelele API-ului: fișierele trebuie să fie valide."""
    try:
        addresses = load_fixture(FIXTURE_PATH)
        registry = load_streets(STREETS_PATH)
    except FileNotFoundError as exc:
        raise ImportScriptError(f"Lipsește un fișier de date: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ImportScriptError(f"JSON invalid într-un fișier de date: {exc}") from exc
    except ValidationError as exc:
        raise ImportScriptError(f"Date invalide într-un fișier de date:\n{exc}") from exc

    known_streets = {street.key for street in registry.streets}
    orphans = sorted({street_key(e.street) for e in addresses} - known_streets)
    print(f"Adrese valide:  {len(addresses)}")
    print(f"Străzi valide:  {len(registry.streets)} ({registry.city}, {registry.county})")
    if orphans:
        print(f"Străzi din adrese care lipsesc din registru: {len(orphans)}")
        for key in orphans[:10]:
            print(f"  - {key}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="import_addresses.py",
        description="Importă străzile și numerele municipiului Suceava din OpenStreetMap.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser(
        "fetch", help="Descarcă datele brute din Overpass în data/osm-cache/."
    )
    fetch_parser.add_argument(
        "--relation-id",
        type=int,
        default=SUCEAVA_RELATION_ID,
        help=f"Relația OSM a localității (implicit {SUCEAVA_RELATION_ID} = Suceava).",
    )

    build_parser_ = subparsers.add_parser(
        "build", help="Scrie data/addresses.fixture.json și data/suceava.streets.json."
    )
    build_parser_.add_argument(
        "--dry-run", action="store_true", help="Arată raportul fără să scrie fișierele."
    )

    subparsers.add_parser("verify", help="Validează fișierele de date deja scrise.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        match args.command:
            case "fetch":
                return _run_fetch(args.relation_id + OSM_AREA_OFFSET)
            case "build":
                return _run_build(dry_run=args.dry_run)
            case "verify":
                return _run_verify()
            case _:
                raise AssertionError(f"Comandă necunoscută: {args.command!r}")
    except ImportScriptError as exc:
        print(f"Eroare: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
