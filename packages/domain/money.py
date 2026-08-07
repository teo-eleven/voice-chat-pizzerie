"""Bani ca întregi în bani (1 leu = 100 bani). Niciun float, nicăieri.

Motivul: `0.1 + 0.2 != 0.3` în float, iar un total greșit cu un ban într-o comandă
reală e un bug pe care nu îl prinzi decât la casă.
"""

from __future__ import annotations

BANI_PER_LEU = 100


def lei_to_bani(lei: str | int) -> int:
    """`"34.90"` sau `"34,90"` -> `3490`. Acceptă și întregi (lei rotunzi)."""
    if isinstance(lei, int):
        return lei * BANI_PER_LEU
    text = lei.strip().replace(",", ".")
    if "." not in text:
        return int(text) * BANI_PER_LEU
    whole, _, frac = text.partition(".")
    frac = (frac + "00")[:2]
    sign = -1 if whole.startswith("-") else 1
    return sign * (abs(int(whole or 0)) * BANI_PER_LEU + int(frac))


def format_ron(bani: int) -> str:
    """`3490` -> `"34,90 lei"`. Pentru afișare pe ecran."""
    sign = "-" if bani < 0 else ""
    whole, frac = divmod(abs(bani), BANI_PER_LEU)
    return f"{sign}{whole},{frac:02d} lei"


def _needs_de(n: int) -> bool:
    """Regula română pentru particula „de" înaintea substantivului numărat.

    Se aplică pentru numere >= 20 ale căror ultime două cifre NU sunt între 1 și 19:
    `20 de lei`, `45 de lei`, `100 de lei`, `199 de lei` — dar `101 lei`, `115 lei`.
    """
    return n >= 20 and not (1 <= n % 100 <= 19)


def _count_noun(n: int, singular: str, plural: str) -> str:
    """`1` + „leu"/"lei" -> `"1 leu"`; `2` -> `"2 lei"`; `45` -> `"45 de lei"`."""
    if n == 1:
        return f"1 {singular}"
    if _needs_de(n):
        return f"{n} de {plural}"
    return f"{n} {plural}"


def spoken_ron(bani: int) -> str:
    """`3490` -> `"34 de lei și 90 de bani"`. Pentru TTS — cifrele citite prost sună rău.

    Rotunjirea la leu se face doar când banii sunt zero; altfel îi rostim, ca să nu
    existe diferență între ce aude clientul și ce scrie pe nota de plată. Acordul
    gramatical (singular/plural, particula „de") respectă regulile limbii române.
    """
    whole, frac = divmod(abs(bani), BANI_PER_LEU)
    lei_part = _count_noun(whole, "leu", "lei")
    if frac == 0:
        return lei_part
    bani_part = _count_noun(frac, "ban", "bani")
    return f"{lei_part} și {bani_part}"


def sum_bani(values: object) -> int:
    """Sumă explicită pe întregi — evită `sum()` cu start float din greșeală."""
    total = 0
    for value in values:  # type: ignore[union-attr]
        total += int(value)
    return total
