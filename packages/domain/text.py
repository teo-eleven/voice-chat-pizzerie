"""Normalizare text pentru potrivirea vorbirii: fără diacritice, fără majuscule.

Folosit la căutarea în catalog după ce a rostit clientul: „Capricciosa" trebuie
găsită din „capriciosa", „CAPRICIOZA" sau „vreau o capricioasă".
"""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    """Lowercase + elimină diacriticele românești + colapsează spațiile.

    Descompunem Unicode (NFKD) și scoatem semnele combinatorii rămase — acoperă
    ă/â/î/ș/ț și variantele lor cu sedilă (ş/ţ) fără o listă manuală de caractere.
    """
    lowered = text.lower()
    decomposed = unicodedata.normalize("NFKD", lowered)
    without_diacritics = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _WHITESPACE_RE.sub(" ", without_diacritics).strip()


def tokens(text: str) -> tuple[str, ...]:
    """Normalizat, apoi despărțit pe caractere non-alfanumerice."""
    normalized = normalize(text)
    return tuple(token for token in _NON_ALNUM_RE.split(normalized) if token)
