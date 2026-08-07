"""Teste unitare pentru packages/domain/delivery_zone.py."""

from __future__ import annotations

import pytest

from packages.domain.delivery_zone import (
    is_in_zone,
    point_in_polygon,
    resolve_from_candidates,
)
from packages.domain.enums import AddressResolution
from packages.domain.errors import DomainError
from packages.domain.models import Address, AddressCandidate, ZoneConfig

#: Patrat simplu (0,0)-(0,10)-(10,10)-(10,0), suficient pentru geometria testata.
_SQUARE = ((0, 0), (0, 10), (10, 10), (10, 0))


def _zone(polygon: tuple[tuple[float, float], ...] = _SQUARE) -> ZoneConfig:
    return ZoneConfig(
        polygon=polygon,
        min_order_bani=5000,
        delivery_fee_bani=1200,
        free_delivery_threshold_bani=12000,
    )


def _address(lat: float | None, lon: float | None, number: str = "1") -> Address:
    return Address(street="Test", number=number, lat=lat, lon=lon)


class TestPointInPolygon:
    def test_clearly_interior_point_is_inside(self):
        # Arrange
        lat, lon = 5, 5

        # Act
        result = point_in_polygon(lat, lon, _SQUARE)

        # Assert
        assert result is True

    def test_clearly_exterior_point_is_outside(self):
        # Arrange
        lat, lon = 20, 20

        # Act
        result = point_in_polygon(lat, lon, _SQUARE)

        # Assert
        assert result is False

    def test_point_on_vertex_is_inside(self):
        # Arrange
        lat, lon = 0, 0

        # Act
        result = point_in_polygon(lat, lon, _SQUARE)

        # Assert
        assert result is True

    def test_point_on_edge_is_inside(self):
        # Arrange
        lat, lon = 0, 5

        # Act
        result = point_in_polygon(lat, lon, _SQUARE)

        # Assert
        assert result is True

    def test_polygon_with_two_points_raises_invalid_polygon(self):
        # Arrange
        polygon = ((0, 0), (1, 1))

        # Act
        with pytest.raises(DomainError) as exc:
            point_in_polygon(1, 1, polygon)

        # Assert
        assert exc.value.issue.code == "invalid_polygon"


class TestIsInZone:
    def test_returns_false_when_lat_is_none(self):
        # Arrange
        address = _address(lat=None, lon=5)
        zone = _zone()

        # Act
        result = is_in_zone(address, zone)

        # Assert
        assert result is False

    def test_returns_false_when_lon_is_none(self):
        # Arrange
        address = _address(lat=5, lon=None)
        zone = _zone()

        # Act
        result = is_in_zone(address, zone)

        # Assert
        assert result is False

    def test_returns_true_for_coordinates_inside_the_zone(self):
        # Arrange
        address = _address(lat=5, lon=5)
        zone = _zone()

        # Act
        result = is_in_zone(address, zone)

        # Assert
        assert result is True

    def test_returns_false_for_coordinates_outside_the_zone(self):
        # Arrange
        address = _address(lat=50, lon=50)
        zone = _zone()

        # Act
        result = is_in_zone(address, zone)

        # Assert
        assert result is False


class TestResolveFromCandidates:
    def test_returns_not_found_when_no_candidates(self):
        # Arrange
        zone = _zone()

        # Act
        result = resolve_from_candidates((), zone)

        # Assert
        assert result.resolution == AddressResolution.NOT_FOUND
        assert result.candidates == ()

    def test_returns_out_of_zone_when_no_candidate_is_inside(self):
        # Arrange
        zone = _zone()
        candidates = (
            AddressCandidate(address=_address(50, 50), confidence=0.9, in_zone=False),
        )

        # Act
        result = resolve_from_candidates(candidates, zone)

        # Assert
        assert result.resolution == AddressResolution.OUT_OF_ZONE
        assert result.candidates == ()

    def test_returns_ok_with_the_single_in_zone_candidate(self):
        # Arrange
        zone = _zone()
        candidates = (
            AddressCandidate(address=_address(5, 5), confidence=0.9, in_zone=False),
        )

        # Act
        result = resolve_from_candidates(candidates, zone)

        # Assert
        assert result.resolution == AddressResolution.OK
        assert len(result.candidates) == 1
        assert result.candidates[0].address.lat == 5
        assert result.candidates[0].in_zone is True

    def test_returns_ambiguous_limited_to_three_sorted_by_confidence_desc(self):
        # Arrange
        zone = _zone()
        candidates = (
            AddressCandidate(address=_address(5, 5, "1"), confidence=0.5, in_zone=False),
            AddressCandidate(address=_address(6, 6, "2"), confidence=0.9, in_zone=False),
            AddressCandidate(address=_address(7, 7, "3"), confidence=0.7, in_zone=False),
            AddressCandidate(address=_address(8, 8, "4"), confidence=0.6, in_zone=False),
            AddressCandidate(address=_address(9, 9, "5"), confidence=0.8, in_zone=False),
        )

        # Act
        result = resolve_from_candidates(candidates, zone)

        # Assert
        assert result.resolution == AddressResolution.AMBIGUOUS
        assert len(result.candidates) == 3
        confidences = [c.confidence for c in result.candidates]
        assert confidences == sorted(confidences, reverse=True)
        assert [c.address.number for c in result.candidates] == ["2", "5", "3"]

    def test_ambiguous_candidates_that_are_out_of_zone_are_excluded_first(self):
        # Arrange
        zone = _zone()
        candidates = (
            AddressCandidate(address=_address(5, 5, "in-1"), confidence=0.9, in_zone=False),
            AddressCandidate(address=_address(6, 6, "in-2"), confidence=0.8, in_zone=False),
            AddressCandidate(address=_address(50, 50, "out"), confidence=0.99, in_zone=False),
        )

        # Act
        result = resolve_from_candidates(candidates, zone)

        # Assert
        assert result.resolution == AddressResolution.AMBIGUOUS
        assert [c.address.number for c in result.candidates] == ["in-1", "in-2"]
