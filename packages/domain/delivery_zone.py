"""Rezolvarea adresei față de zona de livrare.

Decizia dacă un punct e livrabil se ia aici, o singură dată — nu în LLM și nu în UI.
"""

from __future__ import annotations

from .enums import AddressResolution
from .errors import DomainError
from .models import Address, AddressCandidate, AddressResult, ZoneConfig

#: Câți candidați ambigui e rezonabil să-i rostim la telefon.
MAX_SPOKEN_CANDIDATES = 3


def point_in_polygon(lat: float, lon: float, polygon: tuple[tuple[float, float], ...]) -> bool:
    """Ray casting. Un punct pe o latură sau pe un vârf se consideră în zonă.

    Preferăm să primim o comandă la limită decât să refuzăm un client valid.
    """
    if len(polygon) < 3:
        raise DomainError.of("invalid_polygon", "Poligonul zonei are nevoie de cel puțin 3 puncte.")

    if _on_boundary(lat, lon, polygon):
        return True

    inside = False
    count = len(polygon)
    for i in range(count):
        lat1, lon1 = polygon[i]
        lat2, lon2 = polygon[(i + 1) % count]
        crosses_ray = (lon1 > lon) != (lon2 > lon)
        if not crosses_ray:
            continue
        lat_at_lon = lat1 + (lon - lon1) * (lat2 - lat1) / (lon2 - lon1)
        if lat < lat_at_lon:
            inside = not inside
    return inside


def _on_boundary(lat: float, lon: float, polygon: tuple[tuple[float, float], ...]) -> bool:
    """Punctul e pe un vârf sau pe o latură a poligonului."""
    count = len(polygon)
    for i in range(count):
        lat1, lon1 = polygon[i]
        lat2, lon2 = polygon[(i + 1) % count]
        if (lat, lon) == (lat1, lon1):
            return True
        if _on_segment(lat, lon, lat1, lon1, lat2, lon2):
            return True
    return False


def _on_segment(lat: float, lon: float, lat1: float, lon1: float, lat2: float, lon2: float) -> bool:
    """Punctul e coliniar cu segmentul (lat1, lon1)-(lat2, lon2) și între capete."""
    cross = (lon2 - lon1) * (lat - lat1) - (lat2 - lat1) * (lon - lon1)
    if abs(cross) > 1e-12:
        return False
    within_lat = min(lat1, lat2) <= lat <= max(lat1, lat2)
    within_lon = min(lon1, lon2) <= lon <= max(lon1, lon2)
    return within_lat and within_lon


def is_in_zone(address: Address, zone: ZoneConfig) -> bool:
    """`False` dacă lipsesc coordonate — necunoscut nu înseamnă acceptat."""
    if address.lat is None or address.lon is None:
        return False
    return point_in_polygon(address.lat, address.lon, zone.polygon)


def resolve_from_candidates(
    candidates: tuple[AddressCandidate, ...], zone: ZoneConfig
) -> AddressResult:
    """Decide rezoluția adresei, cu mesaj în română, natural pentru rostit."""
    if not candidates:
        return AddressResult(
            resolution=AddressResolution.NOT_FOUND,
            message="Nu am găsit adresa. Îmi puteți spune din nou strada și numărul?",
        )

    rechecked = tuple(
        candidate.model_copy(update={"in_zone": is_in_zone(candidate.address, zone)})
        for candidate in candidates
    )
    in_zone = tuple(c for c in rechecked if c.in_zone)

    if not in_zone:
        return AddressResult(
            resolution=AddressResolution.OUT_OF_ZONE,
            message=(
                "Din păcate nu livrăm în zona aceasta, dar comanda poate fi ridicată "
                "de la restaurant."
            ),
        )

    if len(in_zone) == 1:
        return AddressResult(
            resolution=AddressResolution.OK,
            candidates=in_zone,
            message="Am găsit adresa.",
        )

    top_candidates = tuple(
        sorted(in_zone, key=lambda c: c.confidence, reverse=True)[:MAX_SPOKEN_CANDIDATES]
    )
    return AddressResult(
        resolution=AddressResolution.AMBIGUOUS,
        candidates=top_candidates,
        message="Am găsit mai multe adrese posibile. Care dintre ele este cea corectă?",
    )
