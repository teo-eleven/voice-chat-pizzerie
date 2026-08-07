"""Meniul: cautare si listare pe categorie, cu preturi formatate pentru afisare."""

from __future__ import annotations

from fastapi import APIRouter, Request

from packages.domain import money
from packages.domain.catalog import Catalog
from packages.domain.enums import Category
from packages.domain.models import Product, SizeOption

from .schemas import MenuItemOut, MenuSizeOut

router = APIRouter(prefix="/api", tags=["menu"])


@router.get("/menu", response_model=list[MenuItemOut])
def get_menu(
    request: Request, query: str | None = None, category: Category | None = None
) -> list[MenuItemOut]:
    catalog: Catalog = request.app.state.catalog
    products = catalog.search(query) if query else catalog.products
    if category is not None:
        products = tuple(product for product in products if product.category == category)
    return [_to_menu_item(product) for product in products]


def _to_menu_item(product: Product) -> MenuItemOut:
    return MenuItemOut(
        id=product.id,
        name=product.name,
        category=product.category.value,
        description=product.description,
        ingredients=product.ingredients,
        allergens=tuple(allergen.value for allergen in product.allergens),
        sizes=tuple(_to_menu_size(size) for size in product.sizes),
        price_bani=product.price_bani,
        price_formatted=money.format_ron(product.price_bani) if product.price_bani else None,
        prep_minutes=product.prep_minutes,
        available=product.available,
        aliases=product.aliases,
    )


def _to_menu_size(size: SizeOption) -> MenuSizeOut:
    return MenuSizeOut(
        code=size.code,
        label=size.label,
        diameter_cm=size.diameter_cm,
        price_bani=size.price_bani,
        price_formatted=money.format_ron(size.price_bani),
        oven_slots=size.oven_slots,
        bake_minutes=size.bake_minutes,
    )
