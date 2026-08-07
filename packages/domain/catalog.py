"""Catalogul de produse: încărcare din JSON și căutare tolerantă la voce.

Alături de `config.py`, singurele module care fac I/O în domeniu.
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from . import money, text
from .enums import Category, SizeCode
from .errors import DomainError
from .models import Frozen, Product, SizeOption

#: Rangurile de potrivire ale `search`, în ordinea de sortare a rezultatelor.
_EXACT_NAME_RANK = 0
_ALIAS_RANK = 1
_PARTIAL_RANK = 2

#: Sub acest scor de similaritate (difflib), un token nu se consideră o potrivire parțială.
_PARTIAL_RATIO_THRESHOLD = 0.75
#: Token-urile mai scurte decât atât nu intră în potrivirea parțială (prea multe fals-pozitive).
_MIN_FUZZY_TOKEN_LEN = 3


class Catalog(Frozen):
    """Meniul complet. Imutabil — se construiește o singură dată, la pornire."""

    products: tuple[Product, ...] = ()

    def by_id(self, product_id: str) -> Product | None:
        return next((product for product in self.products if product.id == product_id), None)

    def by_category(self, category: Category) -> tuple[Product, ...]:
        return tuple(product for product in self.products if product.category == category)

    def search(self, query: str) -> tuple[Product, ...]:
        """Potrivire pe `name` și `aliases`, insensibilă la diacritice/majuscule.

        Produsele indisponibile rămân în rezultate, dar sortate la final.
        """
        ranked = [
            (rank, product)
            for product in self.products
            if (rank := _match_rank(query, product)) is not None
        ]
        ranked.sort(key=lambda item: (item[0], not item[1].available))
        return tuple(product for _rank, product in ranked)


def _match_rank(query: str, product: Product) -> int | None:
    query_normalized = text.normalize(query)
    if not query_normalized:
        return None
    if text.normalize(product.name) in query_normalized:
        return _EXACT_NAME_RANK
    if any(text.normalize(alias) in query_normalized for alias in product.aliases):
        return _ALIAS_RANK
    if _has_partial_overlap(text.tokens(query), _candidate_tokens(product)):
        return _PARTIAL_RANK
    return None


def _candidate_tokens(product: Product) -> tuple[str, ...]:
    alias_tokens = tuple(token for alias in product.aliases for token in text.tokens(alias))
    return text.tokens(product.name) + alias_tokens


def _has_partial_overlap(query_tokens: tuple[str, ...], candidate_tokens: tuple[str, ...]) -> bool:
    return any(
        _tokens_overlap(query_token, candidate_token)
        for query_token in query_tokens
        for candidate_token in candidate_tokens
        if len(query_token) >= _MIN_FUZZY_TOKEN_LEN and len(candidate_token) >= _MIN_FUZZY_TOKEN_LEN
    )


def _tokens_overlap(a: str, b: str) -> bool:
    """Egal sau typo apropiat. Fără substring brut — dă fals-pozitive („cola” în „ciocolata”)."""
    if a == b:
        return True
    return SequenceMatcher(None, a, b).ratio() >= _PARTIAL_RATIO_THRESHOLD


def load_catalog(path: Path | str = "data/menu.seed.json") -> Catalog:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    size_template = tuple(raw["size_template"])
    products = tuple(_build_product(item, size_template) for item in raw["products"])
    return Catalog(products=products)


def _build_product(raw: dict[str, Any], size_template: tuple[dict[str, Any], ...]) -> Product:
    category = Category(raw["category"])
    common: dict[str, Any] = {
        "id": raw["id"],
        "name": raw["name"],
        "category": category,
        "description": raw.get("description", ""),
        "ingredients": tuple(raw.get("ingredients", ())),
        "allergens": tuple(raw.get("allergens", ())),
        "prep_minutes": raw.get("prep_minutes", 0),
        "available": raw.get("available", True),
        "aliases": tuple(raw.get("aliases", ())),
    }
    if category == Category.PIZZA:
        return Product(**common, sizes=_build_sizes(raw, size_template))
    return Product(**common, price_bani=_build_price_bani(raw))


def _build_sizes(
    raw: dict[str, Any], size_template: tuple[dict[str, Any], ...]
) -> tuple[SizeOption, ...]:
    prices = raw.get("prices")
    if prices is None:
        raise DomainError.of(
            "pizza_prices_missing", f"Pizza „{raw['name']}” nu are prețuri definite."
        )
    return tuple(_build_size_option(entry, prices, raw["name"]) for entry in size_template)


def _build_size_option(
    entry: dict[str, Any], prices: dict[str, str], product_name: str
) -> SizeOption:
    code = entry["code"]
    price_lei = prices.get(code)
    if price_lei is None:
        raise DomainError.of(
            "pizza_size_missing",
            f"Pizza „{product_name}” nu are preț pentru mărimea „{code}”.",
        )
    return SizeOption(
        code=SizeCode(code),
        label=entry["label"],
        diameter_cm=entry["diameter_cm"],
        price_bani=money.lei_to_bani(price_lei),
        oven_slots=entry["oven_slots"],
        bake_minutes=entry["bake_minutes"],
    )


def _build_price_bani(raw: dict[str, Any]) -> int:
    price = raw.get("price")
    if price is None:
        raise DomainError.of(
            "product_price_missing", f"Produsul „{raw['name']}” nu are preț definit."
        )
    return money.lei_to_bani(price)
