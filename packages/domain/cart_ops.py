"""Operații pe coș: pure, imutabile, re-prețuiesc întreg coșul la final prin `pricing`."""

from __future__ import annotations

from . import pricing, text
from .enums import Fulfillment, SizeCode
from .errors import DomainError
from .limits import MAX_LINES_PER_CART, MAX_QTY_PER_LINE
from .models import Cart, CartLine, Product, ZoneConfig


def add_line(
    cart: Cart,
    product: Product,
    *,
    qty: int = 1,
    size_code: SizeCode | None = None,
    removed_ingredients: tuple[str, ...] = (),
    fulfillment: Fulfillment,
    zone: ZoneConfig,
) -> Cart:
    _validate_cart_size(cart)
    line = _price_line(_next_line_id(cart), product, qty, size_code, removed_ingredients)
    return pricing.price_cart((*cart.lines, line), fulfillment, zone)


def remove_line(cart: Cart, line_id: str, *, fulfillment: Fulfillment, zone: ZoneConfig) -> Cart:
    _find_line(cart, line_id)
    remaining = tuple(line for line in cart.lines if line.line_id != line_id)
    return pricing.price_cart(remaining, fulfillment, zone)


def update_line(
    cart: Cart,
    line_id: str,
    product: Product,
    *,
    qty: int | None = None,
    size_code: SizeCode | None = None,
    removed_ingredients: tuple[str, ...] | None = None,
    fulfillment: Fulfillment,
    zone: ZoneConfig,
) -> Cart:
    existing = _find_line(cart, line_id)
    new_qty = existing.qty if qty is None else qty
    new_size_code = existing.size_code if size_code is None else size_code
    new_removed = (
        existing.removed_ingredients if removed_ingredients is None else removed_ingredients
    )
    updated = _price_line(line_id, product, new_qty, new_size_code, new_removed)
    lines = tuple(updated if line.line_id == line_id else line for line in cart.lines)
    return pricing.price_cart(lines, fulfillment, zone)


def clear_cart(*, fulfillment: Fulfillment, zone: ZoneConfig) -> Cart:
    return pricing.price_cart((), fulfillment, zone)


def _price_line(
    line_id: str,
    product: Product,
    qty: int,
    size_code: SizeCode | None,
    removed_ingredients: tuple[str, ...],
) -> CartLine:
    _validate_availability(product)
    resolved_size_code, size_label, unit_price_bani, oven_slots, bake_minutes = _resolve_size(
        product, size_code
    )
    _validate_qty(qty)
    _validate_removed_ingredients(product, removed_ingredients)
    return CartLine(
        line_id=line_id,
        product_id=product.id,
        product_name=product.name,
        category=product.category,
        qty=qty,
        size_code=resolved_size_code,
        size_label=size_label,
        removed_ingredients=removed_ingredients,
        unit_price_bani=unit_price_bani,
        total_bani=pricing.line_total(unit_price_bani, qty),
        oven_slots=oven_slots,
        bake_minutes=bake_minutes,
        prep_minutes=product.prep_minutes,
    )


def _validate_availability(product: Product) -> None:
    if not product.available:
        raise DomainError.of(
            "product_unavailable",
            f"„{product.name}” nu este disponibil momentan.",
            field="product_id",
        )


def _resolve_size(
    product: Product, size_code: SizeCode | None
) -> tuple[SizeCode | None, str | None, int, int, int]:
    if not product.has_sizes:
        if size_code is not None:
            raise DomainError.of(
                "size_not_applicable",
                f"„{product.name}” nu are mărimi de ales.",
                field="size_code",
            )
        assert product.price_bani is not None, "produs fără mărimi trebuie să aibă price_bani"
        return None, None, product.price_bani, 0, 0
    if size_code is None:
        raise DomainError.of(
            "size_required",
            f"Alege o mărime pentru „{product.name}”: mică, medie sau mare.",
            field="size_code",
        )
    size = product.size(size_code)
    if size is None:
        raise DomainError.of(
            "size_unknown",
            f"„{product.name}” nu are mărimea cerută.",
            field="size_code",
        )
    return size.code, size.label, size.price_bani, size.oven_slots, size.bake_minutes


def _validate_cart_size(cart: Cart) -> None:
    if len(cart.lines) >= MAX_LINES_PER_CART:
        raise DomainError.of(
            "cart_too_large",
            f"Coșul poate avea cel mult {MAX_LINES_PER_CART} de linii. "
            "Pentru comenzi mai mari, vă rugăm așteptați, vă preia un operator.",
            field="line_id",
        )


def _validate_qty(qty: int) -> None:
    if qty <= 0:
        raise DomainError.of("invalid_qty", "Cantitatea trebuie să fie cel puțin 1.", field="qty")
    if qty > MAX_QTY_PER_LINE:
        raise DomainError.of(
            "qty_too_large",
            f"Cantitatea maximă pe o linie este {MAX_QTY_PER_LINE}. "
            "Pentru cantități mai mari, comanda se preia de un operator.",
            field="qty",
        )


def _validate_removed_ingredients(product: Product, removed: tuple[str, ...]) -> None:
    normalized_ingredients = {text.normalize(ingredient) for ingredient in product.ingredients}
    for removed_ingredient in removed:
        if text.normalize(removed_ingredient) not in normalized_ingredients:
            available = ", ".join(product.ingredients)
            raise DomainError.of(
                "ingredient_not_in_product",
                f"„{product.name}” nu are „{removed_ingredient}” ca ingredient. "
                f"Ingredientele sunt: {available}.",
                field="removed_ingredients",
            )


def _find_line(cart: Cart, line_id: str) -> CartLine:
    existing = next((line for line in cart.lines if line.line_id == line_id), None)
    if existing is None:
        raise DomainError.of(
            "line_not_found", f"Linia „{line_id}” nu există în coș.", field="line_id"
        )
    return existing


def _next_line_id(cart: Cart) -> str:
    used_indexes = [int(line.line_id.removeprefix("L")) for line in cart.lines]
    return f"L{max(used_indexes, default=0) + 1}"
