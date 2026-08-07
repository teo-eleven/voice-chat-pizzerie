"""Teste unitare pentru packages/domain/capacity.py.

Foloseste `data/kitchen.config.json` real pentru scenariul numeric complet cerut.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.domain.capacity import (
    can_promise,
    estimate_eta,
    line_slot_minutes,
    order_prep_minutes,
    order_slot_minutes,
    overload_issue,
    queue_slot_minutes,
)
from packages.domain.config import load_kitchen_config
from packages.domain.enums import Category, Fulfillment
from packages.domain.models import CartLine, EtaWindow, KitchenConfig

_KITCHEN_CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "kitchen.config.json"


def _line(
    *, oven_slots: int = 0, bake_minutes: int = 0, qty: int = 1, prep_minutes: int = 0
) -> CartLine:
    return CartLine(
        line_id="L1",
        product_id="P1",
        product_name="Produs",
        category=Category.PIZZA,
        qty=qty,
        unit_price_bani=100,
        total_bani=100 * qty,
        oven_slots=oven_slots,
        bake_minutes=bake_minutes,
        prep_minutes=prep_minutes,
    )


@pytest.fixture
def kitchen_config() -> KitchenConfig:
    return load_kitchen_config(_KITCHEN_CONFIG_PATH)


class TestLineSlotMinutes:
    def test_is_zero_for_line_without_oven_usage(self):
        # Arrange
        line = _line(oven_slots=0, bake_minutes=0, qty=3)

        # Act
        result = line_slot_minutes(line)

        # Assert
        assert result == 0

    def test_multiplies_oven_slots_bake_minutes_and_qty(self):
        # Arrange
        line = _line(oven_slots=3, bake_minutes=10, qty=2)

        # Act
        result = line_slot_minutes(line)

        # Assert
        assert result == 60


class TestOrderSlotMinutes:
    def test_sums_slot_minutes_across_lines(self):
        # Arrange
        lines = (
            _line(oven_slots=1, bake_minutes=7, qty=1),
            _line(oven_slots=2, bake_minutes=8, qty=1),
        )

        # Act
        result = order_slot_minutes(lines)

        # Assert
        assert result == 7 + 16


class TestOrderPrepMinutes:
    def test_returns_the_maximum_not_the_sum(self):
        # Arrange
        lines = (_line(prep_minutes=2), _line(prep_minutes=8))

        # Act
        result = order_prep_minutes(lines)

        # Assert
        assert result == 8

    def test_returns_zero_for_no_lines(self):
        # Arrange
        lines: tuple[CartLine, ...] = ()

        # Act
        result = order_prep_minutes(lines)

        # Assert
        assert result == 0


class TestQueueSlotMinutes:
    def test_sums_slot_minutes_of_all_orders_in_queue(self):
        # Arrange
        order_a = (_line(oven_slots=1, bake_minutes=7, qty=1),)
        order_b = (_line(oven_slots=3, bake_minutes=10, qty=2),)
        queue = (order_a, order_b)

        # Act
        result = queue_slot_minutes(queue)

        # Assert
        assert result == 7 + 60


class TestEstimateEta:
    def test_pickup_with_empty_queue_matches_exact_window(self, kitchen_config: KitchenConfig):
        """oven_slots=3, bake_minutes=10, qty=2, coada 0, PICKUP -> exact 15-25."""
        # Arrange
        lines = (_line(oven_slots=3, bake_minutes=10, qty=2),)

        # Act
        eta = estimate_eta(
            lines, pending_slot_minutes=0, fulfillment=Fulfillment.PICKUP, config=kitchen_config
        )

        # Assert
        assert eta == EtaWindow(min_minutes=15, max_minutes=25)

    def test_delivery_with_empty_queue_matches_exact_window(self, kitchen_config: KitchenConfig):
        """Aceeasi linie, DELIVERY, coada 0 -> exact 30-40."""
        # Arrange
        lines = (_line(oven_slots=3, bake_minutes=10, qty=2),)

        # Act
        eta = estimate_eta(
            lines, pending_slot_minutes=0, fulfillment=Fulfillment.DELIVERY, config=kitchen_config
        )

        # Assert
        assert eta == EtaWindow(min_minutes=30, max_minutes=40)

    def test_delivery_with_pending_queue_matches_exact_window(self, kitchen_config: KitchenConfig):
        """Aceeasi linie, DELIVERY, pending_slot_minutes=400 -> exact 75-85."""
        # Arrange
        lines = (_line(oven_slots=3, bake_minutes=10, qty=2),)

        # Act
        eta = estimate_eta(
            lines, pending_slot_minutes=400, fulfillment=Fulfillment.DELIVERY, config=kitchen_config
        )

        # Assert
        assert eta == EtaWindow(min_minutes=75, max_minutes=85)

    def test_rounds_the_total_upward_to_the_next_multiple(self, kitchen_config: KitchenConfig):
        """27 de minute brute (15 + 12 drum) trebuie rotunjite in sus la 30, nu in jos la 25."""
        # Arrange
        lines = (_line(oven_slots=3, bake_minutes=10, qty=2),)

        # Act
        eta = estimate_eta(
            lines, pending_slot_minutes=0, fulfillment=Fulfillment.DELIVERY, config=kitchen_config
        )

        # Assert
        assert eta.min_minutes == 30
        assert eta.min_minutes % kitchen_config.quote_rounding_minutes == 0


class TestCanPromise:
    def test_true_when_eta_is_exactly_at_the_promisable_limit(self):
        # Arrange
        config = KitchenConfig(
            oven_slots=9, order_overhead_minutes=3, safety_buffer_minutes=5,
            quote_rounding_minutes=5, quote_window_minutes=10,
            max_promisable_minutes=75, delivery_drive_minutes=12,
        )
        eta = EtaWindow(min_minutes=75, max_minutes=85)

        # Act
        result = can_promise(eta, config)

        # Assert
        assert result is True

    def test_false_when_eta_is_one_minute_above_the_limit(self):
        # Arrange
        config = KitchenConfig(
            oven_slots=9, order_overhead_minutes=3, safety_buffer_minutes=5,
            quote_rounding_minutes=5, quote_window_minutes=10,
            max_promisable_minutes=75, delivery_drive_minutes=12,
        )
        eta = EtaWindow(min_minutes=76, max_minutes=86)

        # Act
        result = can_promise(eta, config)

        # Assert
        assert result is False


class TestOverloadIssue:
    def test_returns_none_when_promise_is_possible(self):
        # Arrange
        config = KitchenConfig(
            oven_slots=9, order_overhead_minutes=3, safety_buffer_minutes=5,
            quote_rounding_minutes=5, quote_window_minutes=10,
            max_promisable_minutes=75, delivery_drive_minutes=12,
        )
        eta = EtaWindow(min_minutes=75, max_minutes=85)

        # Act
        result = overload_issue(eta, config)

        # Assert
        assert result is None

    def test_returns_kitchen_overloaded_issue_when_promise_is_not_possible(self):
        # Arrange
        config = KitchenConfig(
            oven_slots=9, order_overhead_minutes=3, safety_buffer_minutes=5,
            quote_rounding_minutes=5, quote_window_minutes=10,
            max_promisable_minutes=75, delivery_drive_minutes=12,
        )
        eta = EtaWindow(min_minutes=90, max_minutes=100)

        # Act
        result = overload_issue(eta, config)

        # Assert
        assert result is not None
        assert result.code == "kitchen_overloaded"
        assert result.field == "eta"
