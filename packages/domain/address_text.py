"""Canonicalizarea numelor de stradă și a numerelor de casă din Suceava.

Aceeași stradă apare în surse sub forme diferite: OpenStreetMap ține „Ştefan cel
Mare" (cu sedilă) și „Strada Ștefan cel Mare" (cu virgulă) ca etichete separate,
iar clientul rostește „Ștefan cel Mare douăzeci și patru", fără cuvântul „Strada".
Toate trei trebuie să ducă la același loc.

Soluția: **potrivirea se face pe cheie, afișarea pe numele canonic.** Cheia
(`street_key`) e numele normalizat fără prefixul de tip; numele canonic
(`canonical_street`) e forma cu prefixul scris întreg, aleasă dintre variante de
`preferred_street_name`.

Modul pur: fără I/O, fără dependențe de framework.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .text import normalize

#: Prefixele de tip, în forma normalizată (fără diacritice, fără punct final) →
#: forma canonică scrisă întreg. Prescurtările sunt aici fiindcă apar și în datele
#: OSM („Str. Zorilor"), și în textul rostit transcris de STT („bd. George Enescu").
STREET_TYPE_ALIASES: dict[str, str] = {
    "strada": "Strada",
    "str": "Strada",
    "stradela": "Stradela",
    "bulevardul": "Bulevardul",
    "bulevard": "Bulevardul",
    "bd": "Bulevardul",
    "bdul": "Bulevardul",
    "b dul": "Bulevardul",
    "calea": "Calea",
    "aleea": "Aleea",
    "piata": "Piața",
    "p ta": "Piața",
    "pta": "Piața",
    "soseaua": "Șoseaua",
    "sos": "Șoseaua",
    "intrarea": "Intrarea",
    "splaiul": "Splaiul",
    "drumul": "Drumul",
    "cartierul": "Cartierul",
}

#: Tipul implicit când numele vine fără prefix („22 Decembrie" în OSM e o stradă).
DEFAULT_STREET_TYPE = "Strada"

#: Caracterele cu sedilă, forma greșită (dar frecventă) a diacriticelor românești.
_CEDILLA_CHARS = frozenset("şţŞŢ")

#: Diacriticele corecte, cu virgulă dedesubt, plus vocalele cu căciulă/breve.
_PROPER_DIACRITICS = frozenset("șțȘȚăâîĂÂÎ")

#: Un număr de casă acceptat: cifre, eventual urmate de un sufix scurt de litere
#: („12A", „1BIS", „64SCA"). Restul se aduce la forma asta de `parse_house_number`.
_HOUSE_NUMBER_RE = re.compile(r"^(?P<digits>\d{1,4})(?P<suffix>[A-Z]{0,4})$")

#: Prima secvență de cifre dintr-un text, cu sufixul de litere lipit de ea.
#:
#: Lookaround-urile fac diferența dintre „a curăța" și „a trunchia": fără ele,
#: „12345" ar deveni tăcut „1234", adică o adresă greșită scrisă în date. Așa,
#: un număr mai lung de patru cifre nu se potrivește deloc și e refuzat pe față.
_LEADING_NUMBER_RE = re.compile(r"(?<!\d)(?P<digits>\d{1,4})(?!\d)(?P<suffix>[A-Za-z]{0,4})")

#: Punctuația care se lipește de prefixul de tip: „Str.", „B-dul", „P-ța".
_PREFIX_PUNCTUATION_RE = re.compile(r"[.\-]+")


def split_street_type(name: str) -> tuple[str | None, str]:
    """Desparte numele în (tip canonic, rest).

    Tipul e `None` când numele nu începe cu un prefix cunoscut — „22 Decembrie"
    rămâne întreg, nu devine tipul „22" cu restul „Decembrie".
    """
    stripped = name.strip()
    if not stripped:
        return None, ""

    words = stripped.split()
    # „B-dul" și „P-ța" sunt un singur cuvânt cu cratimă; „b dul" apare după ce
    # cratima devine spațiu, deci încercăm și varianta din două cuvinte.
    for word_count in (2, 1):
        if len(words) <= word_count:
            continue
        candidate = _prefix_key(" ".join(words[:word_count]))
        canonical_type = STREET_TYPE_ALIASES.get(candidate)
        if canonical_type is not None:
            return canonical_type, " ".join(words[word_count:])
    return None, stripped


def _prefix_key(prefix: str) -> str:
    """Normalizează un posibil prefix de tip: fără diacritice, fără punct/cratimă."""
    return normalize(_PREFIX_PUNCTUATION_RE.sub(" ", prefix))


def street_key(name: str) -> str:
    """Cheia de potrivire: numele normalizat, fără prefixul de tip.

    „Strada Ștefan cel Mare", „Ştefan cel Mare" și „str. stefan cel mare" dau toate
    „stefan cel mare", deci se potrivesc între ele indiferent de sursă.
    """
    _, rest = split_street_type(name)
    return normalize(rest)


def canonical_street(name: str) -> str:
    """Numele de afișat/rostit: prefixul de tip scris întreg, o singură dată.

    Fără prefix în sursă, se pune `DEFAULT_STREET_TYPE` — „22 Decembrie" devine
    „Strada 22 Decembrie", forma pe care o citește livratorul pe bon.
    """
    street_type, rest = split_street_type(name)
    cleaned = " ".join(rest.split())
    if not cleaned:
        return " ".join(name.split())
    return f"{street_type or DEFAULT_STREET_TYPE} {cleaned}"


def preferred_street_name(names: Iterable[str]) -> str:
    """Alege cea mai bună scriere dintre variantele aceleiași străzi.

    Ordinea criteriilor: cu prefix de tip explicit, apoi cu diacritice corecte
    (ș/ț cu virgulă), apoi fără sedile, apoi cel mai lung. La egalitate perfectă
    decide ordinea alfabetică, ca importul să fie reproductibil — un rezultat care
    depinde de ordinea de citire a datelor OSM ar face fiecare re-import un diff.
    """
    candidates = [name.strip() for name in names if name and name.strip()]
    if not candidates:
        raise ValueError("preferred_street_name are nevoie de cel puțin un nume.")
    return canonical_street(max(candidates, key=_variant_rank))


def _variant_rank(name: str) -> tuple[int, int, int, int, str]:
    """Scorul unei variante de scriere; mai mare = mai bun. Vezi criteriile de sus."""
    street_type, _ = split_street_type(name)
    proper = sum(1 for char in name if char in _PROPER_DIACRITICS)
    cedillas = sum(1 for char in name if char in _CEDILLA_CHARS)
    # Numele intră negat în cheie: `max` ia cel mai mare scor, dar alfabetic vrem
    # primul nume, nu ultimul.
    return (
        int(street_type is not None),
        proper,
        -cedillas,
        len(name),
        _negated_alphabetical(name),
    )


def _negated_alphabetical(name: str) -> str:
    """Inversează ordinea alfabetică, ca `max` să aleagă primul nume, nu ultimul."""
    return "".join(chr(0x10FFFF - ord(char)) for char in normalize(name))


def parse_house_number(raw: str) -> str | None:
    """Curăță un număr de casă din date brute; `None` dacă nu conține niciun număr.

    OSM ține valori scrise de oameni: „nr 140", „4-6", „38/2", „1 T 49", „F.N."
    (fără număr). Păstrăm prima secvență de cifre cu sufixul lipit de ea — „4-6"
    devine „4", care e numărul de la care începe imobilul — și refuzăm restul.
    Un interval păstrat întreg n-ar fi găsit niciodată de un client care rostește
    un singur număr.
    """
    match = _LEADING_NUMBER_RE.search(raw)
    if match is None:
        return None
    digits = match.group("digits").lstrip("0") or "0"
    return f"{digits}{match.group('suffix').upper()}"


def house_number_key(raw: str) -> str:
    """Cheia de potrivire a numărului: fără spații, litere mari, fără zerouri în față.

    „12 a", „12A" și „012a" sunt același număr; „12" și „12A" nu sunt.
    """
    compact = "".join(raw.split()).upper()
    match = _HOUSE_NUMBER_RE.match(compact)
    if match is None:
        return normalize(compact)
    digits = match.group("digits").lstrip("0") or "0"
    return f"{digits}{match.group('suffix')}".lower()
