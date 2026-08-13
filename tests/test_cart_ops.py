"""Teste unitare pentru packages/domain/cart_ops.py."""

from __future__ import annotations

import pytest

from packages.domain import cart_ops
from packages.domain.enums import Category, Fulfillment, SizeCode
from packages.domain.errors import DomainError
from packages.domain.limits import MAX_LINES_PER_CART, MAX_QTY_PER_LINE
from packages.domain.models import (
    Cart,
    LineChanges,
    LineSpec,
    PricingContext,
    Product,
    SizeOption,
    ZoneConfig,
)


def _zone() -> ZoneConfig:
    return ZoneConfig(
        polygon=((0, 0), (0, 10), (10, 10), (10, 0)),
        min_order_bani=5000,
        delivery_fee_bani=1200,
        free_delivery_threshold_bani=12000,
    )


def _context(fulfillment: Fulfillment = Fulfillment.PICKUP) -> PricingContext:
    return PricingContext(fulfillment=fulfillment, zone=_zone())


def _pizza(available: bool = True) -> Product:
    return Product(
        id="PZ-TEST",
        name="Test Pizza",
        category=Category.PIZZA,
        ingredients=("sos de rosii", "mozzarella", "sunca"),
        available=available,
        sizes=(
            SizeOption(
                code=SizeCode.SMALL, label="mica", diameter_cm=25,
                price_bani=3000, oven_slots=1, bake_minutes=7,
            ),
            SizeOption(
                code=SizeCode.MEDIUM, label="medie", diameter_cm=32,
                price_bani=4000, oven_slots=2, bake_minutes=8,
            ),
        ),
    )


def _drink(available: bool = True) -> Product:
    return Product(
        id="BT-TEST", name="Test Drink", category=Category.DRINK,
        price_bani=500, available=available,
    )


class TestAddLine:
    def test_adds_pizza_line_with_chosen_size(self):
        # Arrange
        cart = Cart()
        pizza = _pizza()
        context = _context()

        # Act
        result = cart_ops.add_line(
            cart, pizza, spec=LineSpec(size_code=SizeCode.SMALL), context=context
        )

        # Assert
        assert len(result.lines) == 1
        line = result.lines[0]
        assert line.product_id == "PZ-TEST"
        assert line.size_code == SizeCode.SMALL
        assert line.unit_price_bani == 3000
        assert line.total_bani == 3000

    def test_rejects_unavailable_product(self):
        # Arrange
        cart = Cart()
        pizza = _pizza(available=False)
        context = _context()

        # Act
        with pytest.raises(DomainError) as exc:
            cart_ops.add_line(
                cart, pizza, spec=LineSpec(size_code=SizeCode.SMALL), context=context
            )

        # Assert
        assert exc.value.issue.code == "product_unavailable"

    def test_rejects_pizza_without_size(self):
        # Arrange
        cart = Cart()
        pizza = _pizza()
        context = _context()

        # Act
        with pytest.raises(DomainError) as exc:
            cart_ops.add_line(cart, pizza, spec=LineSpec(), context=context)

        # Assert
        assert exc.value.issue.code == "size_required"

    def test_rejects_unknown_size_code_not_offered_by_product(self):
        # Arrange
        cart = Cart()
        pizza = _pizza()  # nu are marimea "large" in template
        context = _context()

        # Act
        with pytest.raises(DomainError) as exc:
            cart_ops.add_line(
                cart, pizza, spec=LineSpec(size_code=SizeCode.LARGE), context=context
            )

        # Assert
        assert exc.value.issue.code == "size_unknown"

    def test_rejects_size_for_product_without_sizes(self):
        # Arrange
        cart = Cart()
        drink = _drink()
        context = _context()

        # Act
        with pytest.raises(DomainError) as exc:
            cart_ops.add_line(
                cart, drink, spec=LineSpec(size_code=SizeCode.SMALL), context=context
            )

        # Assert
        assert exc.value.issue.code == "size_not_applicable"

    @pytest.mark.parametrize("qty", [0, -1])
    def test_rejects_invalid_qty(self, qty: int):
        # Arrange
        cart = Cart()
        drink = _drink()
        context = _context()

        # Act
        with pytest.raises(DomainError) as exc:
            cart_ops.add_line(cart, drink, spec=LineSpec(qty=qty), context=context)

        # Assert
        assert exc.value.issue.code == "invalid_qty"

    def test_rejects_removed_ingredient_not_in_product(self):
        # Arrange
        cart = Cart()
        pizza = _pizza()
        context = _context()

        # Act
        with pytest.raises(DomainError) as exc:
            cart_ops.add_line(
                cart, pizza,
                spec=LineSpec(size_code=SizeCode.SMALL, removed_ingredients=("lamaie",)),
                context=context,
            )

        # Assert
        assert exc.value.issue.code == "ingredient_not_in_product"

    def test_accepts_removed_ingredient_matching_by_name(self):
        # Arrange
        cart = Cart()
        pizza = _pizza()
        context = _context()

        # Act
        result = cart_ops.add_line(
            cart, pizza,
            spec=LineSpec(size_code=SizeCode.SMALL, removed_ingredients=("sunca",)),
            context=context,
        )

        # Assert
        assert result.lines[0].removed_ingredients == ("sunca",)

    def test_accepts_removed_ingredient_regardless_of_diacritics_and_case(self):
        # Arrange
        cart = Cart()
        pizza = _pizza()  # ingredient real: "sunca" (fara diacritice in fixture)
        context = _context()

        # Act
        result = cart_ops.add_line(
            cart, pizza,
            spec=LineSpec(size_code=SizeCode.SMALL, removed_ingredients=("SUNCĂ",)),
            context=context,
        )

        # Assert: acceptat, chiar daca varianta rostita are diacritice si majuscule
        assert result.lines[0].removed_ingredients == ("SUNCĂ",)

    def test_line_ids_are_deterministic_l1_then_l2(self):
        # Arrange
        cart = Cart()
        pizza = _pizza()
        drink = _drink()
        context = _context()

        # Act
        after_first = cart_ops.add_line(
            cart, pizza, spec=LineSpec(size_code=SizeCode.SMALL), context=context
        )
        after_second = cart_ops.add_line(
            after_first, drink, spec=LineSpec(), context=context
        )

        # Assert
        assert [line.line_id for line in after_second.lines] == ["L1", "L2"]

    def test_does_not_mutate_the_original_cart(self):
        # Arrange
        cart = Cart()
        pizza = _pizza()
        context = _context()

        # Act
        result = cart_ops.add_line(
            cart, pizza, spec=LineSpec(size_code=SizeCode.SMALL), context=context
        )

        # Assert
        assert cart.lines == ()
        assert cart.items_bani == 0
        assert result is not cart
        assert len(result.lines) == 1


class TestRemoveLine:
    def test_removes_the_matching_line(self):
        # Arrange
        context = _context()
        cart = cart_ops.add_line(
            Cart(), _pizza(), spec=LineSpec(size_code=SizeCode.SMALL),
            context=context,
        )

        # Act
        result = cart_ops.remove_line(cart, "L1", context=context)

        # Assert
        assert result.lines == ()
        assert result.items_bani == 0

    def test_raises_line_not_found_for_unknown_line_id(self):
        # Arrange
        context = _context()
        cart = Cart()

        # Act
        with pytest.raises(DomainError) as exc:
            cart_ops.remove_line(cart, "L99", context=context)

        # Assert
        assert exc.value.issue.code == "line_not_found"

    def test_does_not_mutate_the_original_cart(self):
        # Arrange
        context = _context()
        cart = cart_ops.add_line(
            Cart(), _pizza(), spec=LineSpec(size_code=SizeCode.SMALL),
            context=context,
        )

        # Act
        result = cart_ops.remove_line(cart, "L1", context=context)

        # Assert
        assert len(cart.lines) == 1
        assert result is not cart

    def test_next_line_id_does_not_recycle_a_removed_lower_id(self):
        """Dupa ce L1 e scos, urmatoarea linie adaugata trebuie sa fie L3, nu L1."""
        # Arrange
        context = _context()
        cart = cart_ops.add_line(
            Cart(), _pizza(), spec=LineSpec(size_code=SizeCode.SMALL),
            context=context,
        )
        cart = cart_ops.add_line(cart, _drink(), spec=LineSpec(), context=context)
        cart = cart_ops.remove_line(cart, "L1", context=context)

        # Act
        result = cart_ops.add_line(cart, _drink(), spec=LineSpec(), context=context)

        # Assert
        assert [line.line_id for line in result.lines] == ["L2", "L3"]


class TestUpdateLine:
    def test_updating_only_qty_preserves_other_fields(self):
        # Arrange
        context = _context()
        pizza = _pizza()
        cart = cart_ops.add_line(
            Cart(), pizza,
            spec=LineSpec(size_code=SizeCode.SMALL, removed_ingredients=("sunca",)),
            context=context,
        )

        # Act
        result = cart_ops.update_line(
            cart, "L1", pizza, changes=LineChanges(qty=3), context=context
        )

        # Assert
        updated_line = result.lines[0]
        assert updated_line.qty == 3
        assert updated_line.size_code == SizeCode.SMALL
        assert updated_line.removed_ingredients == ("sunca",)
        assert updated_line.total_bani == 9000

    def test_updating_size_code_recomputes_unit_price(self):
        # Arrange
        context = _context()
        pizza = _pizza()
        cart = cart_ops.add_line(
            Cart(), pizza, spec=LineSpec(size_code=SizeCode.SMALL), context=context
        )

        # Act
        result = cart_ops.update_line(
            cart, "L1", pizza, changes=LineChanges(size_code=SizeCode.MEDIUM),
            context=context,
        )

        # Assert
        assert result.lines[0].size_code == SizeCode.MEDIUM
        assert result.lines[0].unit_price_bani == 4000

    def test_raises_line_not_found_for_unknown_line_id(self):
        # Arrange
        context = _context()
        cart = Cart()
        pizza = _pizza()

        # Act
        with pytest.raises(DomainError) as exc:
            cart_ops.update_line(
                cart, "L99", pizza, changes=LineChanges(qty=2), context=context
            )

        # Assert
        assert exc.value.issue.code == "line_not_found"

    def test_does_not_mutate_the_original_cart(self):
        # Arrange
        context = _context()
        pizza = _pizza()
        cart = cart_ops.add_line(
            Cart(), pizza, spec=LineSpec(size_code=SizeCode.SMALL), context=context
        )

        # Act
        result = cart_ops.update_line(
            cart, "L1", pizza, changes=LineChanges(qty=5), context=context
        )

        # Assert
        assert cart.lines[0].qty == 1
        assert result.lines[0].qty == 5
        assert result is not cart


class TestQtyLimit:
    def test_accepts_qty_exactly_at_max_qty_per_line(self):
        # Arrange
        cart = Cart()
        drink = _drink()
        context = _context()

        # Act
        result = cart_ops.add_line(
            cart, drink, spec=LineSpec(qty=MAX_QTY_PER_LINE), context=context
        )

        # Assert
        assert result.lines[0].qty == MAX_QTY_PER_LINE

    def test_rejects_qty_above_max_on_add_line(self):
        # Arrange
        cart = Cart()
        drink = _drink()
        context = _context()

        # Act
        with pytest.raises(DomainError) as exc:
            cart_ops.add_line(
                cart, drink, spec=LineSpec(qty=MAX_QTY_PER_LINE + 1),
                context=context,
            )

        # Assert
        assert exc.value.issue.code == "qty_too_large"

    def test_rejects_qty_above_max_on_update_line(self):
        # Arrange
        context = _context()
        drink = _drink()
        cart = cart_ops.add_line(Cart(), drink, spec=LineSpec(), context=context)

        # Act
        with pytest.raises(DomainError) as exc:
            cart_ops.update_line(
                cart, "L1", drink, changes=LineChanges(qty=MAX_QTY_PER_LINE + 1),
                context=context,
            )

        # Assert
        assert exc.value.issue.code == "qty_too_large"


class TestCartSizeLimit:
    def test_rejects_add_line_when_cart_is_already_at_max_lines(self):
        # Arrange: umplem cosul pana la plafonul de linii
        context = _context()
        drink = _drink()
        cart = Cart()
        for _ in range(MAX_LINES_PER_CART):
            cart = cart_ops.add_line(cart, drink, spec=LineSpec(), context=context)

        # Act
        with pytest.raises(DomainError) as exc:
            cart_ops.add_line(cart, drink, spec=LineSpec(), context=context)

        # Assert
        assert exc.value.issue.code == "cart_too_large"

    def test_update_line_still_works_when_cart_is_at_max_lines(self):
        # Arrange: cosul plin nu creste, deci update_line nu trebuie blocat
        context = _context()
        drink = _drink()
        cart = Cart()
        for _ in range(MAX_LINES_PER_CART):
            cart = cart_ops.add_line(cart, drink, spec=LineSpec(), context=context)

        # Act
        result = cart_ops.update_line(
            cart, "L1", drink, changes=LineChanges(qty=3), context=context
        )

        # Assert: numarul de linii ramane neschimbat, doar cantitatea liniei L1
        assert len(result.lines) == MAX_LINES_PER_CART
        assert result.lines[0].qty == 3


class TestClearCart:
    def test_returns_empty_cart_with_zero_totals(self):
        # Arrange
        context = _context(fulfillment=Fulfillment.DELIVERY)

        # Act
        result = cart_ops.clear_cart(context=context)

        # Assert
        assert result.lines == ()
        assert result.items_bani == 0
        assert result.delivery_fee_bani == 0
        assert result.total_bani == 0
