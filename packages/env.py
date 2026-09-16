"""Incarcarea fisierului `.env` din radacina proiectului.

`.env.example` promite „copiaza in .env si completeaza", dar nimic nu citea
fisierul: cheile ajungeau pe disc si nicaieri altundeva. Se cheama explicit din
punctele de intrare (API si agent), nu ca efect secundar la import — un import
care umbla in mediul procesului e greu de depanat si imposibil de izolat in teste.

Variabilele deja setate in mediu au prioritate (`override=False`): un
`DATABASE_URL` pus de teste sau de CI nu trebuie sa poata fi suprascris de un
`.env` uitat pe masina cuiva.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    """Incarca `.env` din radacina proiectului, daca exista. Idempotenta."""
    load_dotenv(_PROJECT_ROOT / ".env", override=False)
