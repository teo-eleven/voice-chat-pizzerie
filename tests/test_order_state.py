"""Teste unitare pentru packages/domain/order_state.py."""

from __future__ import annotations

import pytest

from packages.domain.enums import Fulfillment, OrderStatus, PaymentMethod
from packages.domain.errors import DomainError
from packages.domain.models import Cart, Contact, Order
from packages.domain.order_state import (
    can_transition,
    is_final,
    is_visible_to_driver,
    is_visible_to_kitchen,
    transition,
)


def _order(status: OrderStatus, fulfillment: Fulfillment) -> Order:
    return Order(
        id="ORD-1",
        cart=Cart(),
        fulfillment=fulfillment,
        contact=Contact(phone="0700000000"),
        payment=PaymentMethod.CASH,
        status=status,
    )


class TestAllowedTransitions:
    def test_new_to_in_kitchen_is_allowed(self):
        # Arrange
        order = _order(OrderStatus.NEW, Fulfillment.PICKUP)

        # Act
        result = transition(order, OrderStatus.IN_KITCHEN)

        # Assert
        assert result.status == OrderStatus.IN_KITCHEN

    def test_new_to_cancelled_is_allowed(self):
        # Arrange
        order = _order(OrderStatus.NEW, Fulfillment.PICKUP)

        # Act
        result = transition(order, OrderStatus.CANCELLED)

        # Assert
        assert result.status == OrderStatus.CANCELLED

    def test_in_kitchen_to_ready_is_allowed(self):
        # Arrange
        order = _order(OrderStatus.IN_KITCHEN, Fulfillment.DELIVERY)

        # Act
        result = transition(order, OrderStatus.READY)

        # Assert
        assert result.status == OrderStatus.READY

    def test_in_kitchen_to_cancelled_is_allowed(self):
        # Arrange
        order = _order(OrderStatus.IN_KITCHEN, Fulfillment.DELIVERY)

        # Act
        result = transition(order, OrderStatus.CANCELLED)

        # Assert
        assert result.status == OrderStatus.CANCELLED

    def test_ready_to_assigned_is_allowed_for_delivery(self):
        # Arrange
        order = _order(OrderStatus.READY, Fulfillment.DELIVERY)

        # Act
        result = transition(order, OrderStatus.ASSIGNED)

        # Assert
        assert result.status == OrderStatus.ASSIGNED

    def test_ready_to_picked_up_is_allowed_for_pickup(self):
        # Arrange
        order = _order(OrderStatus.READY, Fulfillment.PICKUP)

        # Act
        result = transition(order, OrderStatus.PICKED_UP)

        # Assert
        assert result.status == OrderStatus.PICKED_UP

    def test_ready_to_cancelled_is_allowed(self):
        # Arrange
        order = _order(OrderStatus.READY, Fulfillment.PICKUP)

        # Act
        result = transition(order, OrderStatus.CANCELLED)

        # Assert
        assert result.status == OrderStatus.CANCELLED

    def test_assigned_to_out_is_allowed_for_delivery(self):
        # Arrange
        order = _order(OrderStatus.ASSIGNED, Fulfillment.DELIVERY)

        # Act
        result = transition(order, OrderStatus.OUT)

        # Assert
        assert result.status == OrderStatus.OUT

    def test_assigned_to_cancelled_is_allowed_for_delivery(self):
        # Arrange
        order = _order(OrderStatus.ASSIGNED, Fulfillment.DELIVERY)

        # Act
        result = transition(order, OrderStatus.CANCELLED)

        # Assert
        assert result.status == OrderStatus.CANCELLED

    def test_out_to_delivered_is_allowed_for_delivery(self):
        # Arrange
        order = _order(OrderStatus.OUT, Fulfillment.DELIVERY)

        # Act
        result = transition(order, OrderStatus.DELIVERED)

        # Assert
        assert result.status == OrderStatus.DELIVERED


class TestDisallowedTransitions:
    def test_raises_invalid_transition_for_edge_not_in_table(self):
        # Arrange
        order = _order(OrderStatus.NEW, Fulfillment.PICKUP)

        # Act
        with pytest.raises(DomainError) as exc:
            transition(order, OrderStatus.READY)

        # Assert
        assert exc.value.issue.code == "invalid_transition"

    def test_raises_invalid_transition_from_a_final_state(self):
        # Arrange
        order = _order(OrderStatus.DELIVERED, Fulfillment.DELIVERY)

        # Act
        with pytest.raises(DomainError) as exc:
            transition(order, OrderStatus.IN_KITCHEN)

        # Assert
        assert exc.value.issue.code == "invalid_transition"

    def test_picked_up_on_a_delivery_order_raises_fulfillment_mismatch(self):
        # Arrange
        order = _order(OrderStatus.READY, Fulfillment.DELIVERY)

        # Act
        with pytest.raises(DomainError) as exc:
            transition(order, OrderStatus.PICKED_UP)

        # Assert
        assert exc.value.issue.code == "fulfillment_mismatch"

    def test_assigned_on_a_pickup_order_raises_fulfillment_mismatch(self):
        # Arrange
        order = _order(OrderStatus.READY, Fulfillment.PICKUP)

        # Act
        with pytest.raises(DomainError) as exc:
            transition(order, OrderStatus.ASSIGNED)

        # Assert
        assert exc.value.issue.code == "fulfillment_mismatch"


class TestTransitionImmutability:
    def test_returns_a_new_order_and_does_not_mutate_the_original(self):
        # Arrange
        order = _order(OrderStatus.NEW, Fulfillment.PICKUP)

        # Act
        result = transition(order, OrderStatus.IN_KITCHEN)

        # Assert
        assert order.status == OrderStatus.NEW
        assert result.status == OrderStatus.IN_KITCHEN
        assert result is not order


class TestIsVisibleToKitchen:
    @pytest.mark.parametrize(
        "status", [OrderStatus.NEW, OrderStatus.IN_KITCHEN, OrderStatus.READY]
    )
    @pytest.mark.parametrize("fulfillment", [Fulfillment.DELIVERY, Fulfillment.PICKUP])
    def test_true_for_pre_handoff_statuses_on_both_fulfillment_types(
        self, status: OrderStatus, fulfillment: Fulfillment
    ):
        # Arrange
        order = _order(status, fulfillment)

        # Act
        result = is_visible_to_kitchen(order)

        # Assert
        assert result is True

    def test_false_for_delivered_order(self):
        # Arrange
        order = _order(OrderStatus.DELIVERED, Fulfillment.DELIVERY)

        # Act
        result = is_visible_to_kitchen(order)

        # Assert
        assert result is False

    def test_false_for_picked_up_order(self):
        # Arrange
        order = _order(OrderStatus.PICKED_UP, Fulfillment.PICKUP)

        # Act
        result = is_visible_to_kitchen(order)

        # Assert
        assert result is False


class TestIsVisibleToDriver:
    @pytest.mark.parametrize(
        "status", [OrderStatus.READY, OrderStatus.ASSIGNED, OrderStatus.OUT]
    )
    def test_true_for_delivery_order_in_driver_visible_statuses(self, status: OrderStatus):
        # Arrange
        order = _order(status, Fulfillment.DELIVERY)

        # Act
        result = is_visible_to_driver(order)

        # Assert
        assert result is True

    def test_false_for_delivered_order(self):
        # Arrange
        order = _order(OrderStatus.DELIVERED, Fulfillment.DELIVERY)

        # Act
        result = is_visible_to_driver(order)

        # Assert
        assert result is False

    @pytest.mark.parametrize(
        "status",
        [
            OrderStatus.NEW,
            OrderStatus.IN_KITCHEN,
            OrderStatus.READY,
            OrderStatus.PICKED_UP,
            OrderStatus.CANCELLED,
        ],
    )
    def test_false_for_any_pickup_order_status(self, status: OrderStatus):
        # Arrange
        order = _order(status, Fulfillment.PICKUP)

        # Act
        result = is_visible_to_driver(order)

        # Assert
        assert result is False


class TestCanTransition:
    def test_true_for_allowed_transition_matching_fulfillment(self):
        # Arrange
        order = _order(OrderStatus.READY, Fulfillment.DELIVERY)

        # Act
        result = can_transition(order, OrderStatus.ASSIGNED)

        # Assert
        assert result is True

    def test_false_for_transition_not_in_the_table(self):
        # Arrange
        order = _order(OrderStatus.NEW, Fulfillment.PICKUP)

        # Act
        result = can_transition(order, OrderStatus.READY)

        # Assert
        assert result is False

    def test_false_for_allowed_edge_with_mismatched_fulfillment(self):
        # Arrange
        order = _order(OrderStatus.READY, Fulfillment.PICKUP)

        # Act
        result = can_transition(order, OrderStatus.ASSIGNED)

        # Assert
        assert result is False


class TestIsFinal:
    @pytest.mark.parametrize(
        "status", [OrderStatus.DELIVERED, OrderStatus.PICKED_UP, OrderStatus.CANCELLED]
    )
    def test_true_for_terminal_statuses(self, status: OrderStatus):
        # Arrange (statusul e parametrizat)

        # Act
        result = is_final(status)

        # Assert
        assert result is True

    @pytest.mark.parametrize(
        "status",
        [
            OrderStatus.NEW,
            OrderStatus.IN_KITCHEN,
            OrderStatus.READY,
            OrderStatus.ASSIGNED,
            OrderStatus.OUT,
        ],
    )
    def test_false_for_non_terminal_statuses(self, status: OrderStatus):
        # Arrange (statusul e parametrizat)

        # Act
        result = is_final(status)

        # Assert
        assert result is False
