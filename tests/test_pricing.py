"""Teste unitare pentru packages/domain/pricing.py."""

from __future__ import annotations

from packages.domain.enums import Category, Fulfillment
from packages.domain.models import CartLine, ZoneConfig
from packages.domain.pricing import check_minimum, delivery_fee, line_total, price_cart


def _zone(
    *,
    min_order_bani: int = 5000,
    delivery_fee_bani: int = 1200,
    free_delivery_threshold_bani: int | None = 12000,
) -> ZoneConfig:
    return ZoneConfig(
        polygon=((0, 0), (0, 10), (10, 10), (10, 0)),
        min_order_bani=min_order_bani,
        delivery_fee_bani=delivery_fee_bani,
        free_delivery_threshold_bani=free_delivery_threshold_bani,
    )


def _line(line_id: str, unit_price_bani: int, qty: int) -> CartLine:
    return CartLine(
        line_id=line_id,
        product_id="P1",
        product_name="Produs",
        category=Category.PIZZA,
        qty=qty,
        unit_price_bani=unit_price_bani,
        total_bani=unit_price_bani * qty,
    )


class TestLineTotal:
    def test_multiplies_unit_price_by_quantity(self):
        # Arrange
        unit_price_bani = 3400
        qty = 3

        # Act
        result = line_total(unit_price_bani, qty)

        # Assert
        assert result == 10200

    def test_returns_unit_price_for_quantity_one(self):
        # Arrange
        unit_price_bani = 4500
        qty = 1

        # Act
        result = line_total(unit_price_bani, qty)

        # Assert
        assert result == 4500


class TestDeliveryFee:
    def test_is_zero_for_pickup_regardless_of_amount(self):
        # Arrange
        zone = _zone()

        # Act
        result = delivery_fee(0, Fulfillment.PICKUP, zone)

        # Assert
        assert result == 0

    def test_is_zero_above_free_delivery_threshold(self):
        # Arrange
        zone = _zone(free_delivery_threshold_bani=12000)
        items_bani = 12500

        # Act
        result = delivery_fee(items_bani, Fulfillment.DELIVERY, zone)

        # Assert
        assert result == 0

    def test_is_zero_at_exact_free_delivery_threshold(self):
        # Arrange
        zone = _zone(free_delivery_threshold_bani=12000)
        items_bani = 12000

        # Act
        result = delivery_fee(items_bani, Fulfillment.DELIVERY, zone)

        # Assert
        assert result == 0

    def test_is_applied_below_free_delivery_threshold(self):
        # Arrange
        zone = _zone(free_delivery_threshold_bani=12000, delivery_fee_bani=1200)
        items_bani = 11999

        # Act
        result = delivery_fee(items_bani, Fulfillment.DELIVERY, zone)

        # Assert
        assert result == 1200

    def test_is_applied_when_zone_has_no_free_delivery_threshold(self):
        # Arrange
        zone = _zone(free_delivery_threshold_bani=None, delivery_fee_bani=1200)
        items_bani = 999999

        # Act
        result = delivery_fee(items_bani, Fulfillment.DELIVERY, zone)

        # Assert
        assert result == 1200


class TestPriceCart:
    def test_recalculates_items_and_total_bani_from_lines(self):
        # Arrange
        zone = _zone(free_delivery_threshold_bani=12000, delivery_fee_bani=1200)
        lines = (_line("L1", 3400, 2), _line("L2", 800, 1))

        # Act
        cart = price_cart(lines, Fulfillment.DELIVERY, zone)

        # Assert
        assert cart.items_bani == 7600
        assert cart.delivery_fee_bani == 1200
        assert cart.total_bani == 8800

    def test_recalculates_total_with_zero_fee_for_pickup(self):
        # Arrange
        zone = _zone()
        lines = (_line("L1", 3400, 1),)

        # Act
        cart = price_cart(lines, Fulfillment.PICKUP, zone)

        # Assert
        assert cart.items_bani == 3400
        assert cart.delivery_fee_bani == 0
        assert cart.total_bani == 3400

    def test_prices_empty_lines_as_empty_cart(self):
        # Arrange
        zone = _zone()

        # Act
        cart = price_cart((), Fulfillment.PICKUP, zone)

        # Assert
        assert cart.lines == ()
        assert cart.items_bani == 0
        assert cart.total_bani == 0


class TestCheckMinimum:
    def test_returns_none_for_pickup_regardless_of_amount(self):
        # Arrange
        zone = _zone(min_order_bani=5000)
        cart = price_cart((_line("L1", 100, 1),), Fulfillment.PICKUP, zone)

        # Act
        result = check_minimum(cart, Fulfillment.PICKUP, zone)

        # Assert
        assert result is None

    def test_returns_issue_when_delivery_order_is_below_minimum(self):
        # Arrange
        zone = _zone(
            min_order_bani=5000, delivery_fee_bani=1200, free_delivery_threshold_bani=12000
        )
        cart = price_cart((_line("L1", 4000, 1),), Fulfillment.DELIVERY, zone)

        # Act
        result = check_minimum(cart, Fulfillment.DELIVERY, zone)

        # Assert
        assert result is not None
        assert result.code == "below_minimum_order"
        assert result.field == "items_bani"

    def test_returns_none_when_delivery_order_meets_minimum_exactly(self):
        # Arrange
        zone = _zone(min_order_bani=5000)
        cart = price_cart((_line("L1", 5000, 1),), Fulfillment.DELIVERY, zone)

        # Act
        result = check_minimum(cart, Fulfillment.DELIVERY, zone)

        # Assert
        assert result is None

    def test_compares_against_items_bani_not_total_bani_with_delivery_fee(self):
        """items_bani=4000 < min=5000, dar total_bani (cu taxa de 1200) = 5200 >= min.

        Verdictul trebuie sa ramana "sub minim": comanda minima se compara pe
        items_bani, fara taxa de livrare adaugata la total.
        """
        # Arrange
        zone = _zone(
            min_order_bani=5000, delivery_fee_bani=1200, free_delivery_threshold_bani=12000
        )
        cart = price_cart((_line("L1", 4000, 1),), Fulfillment.DELIVERY, zone)

        # Act
        result = check_minimum(cart, Fulfillment.DELIVERY, zone)

        # Assert
        assert cart.total_bani == 5200  # peste minim daca ai include taxa, gresit
        assert result is not None
        assert result.code == "below_minimum_order"
