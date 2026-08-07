"""Încărcare configurație de pe disc: capacitatea bucătăriei și zona de livrare.

Alături de `catalog.py`, singurele module care fac I/O în domeniu.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import KitchenConfig, ZoneConfig


def _read_json(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _drop_comment_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Scoate cheile de comentariu (`_comment` etc.) — modelele au `extra="forbid"`."""
    return {key: value for key, value in data.items() if not key.startswith("_")}


def load_kitchen_config(path: Path | str = "data/kitchen.config.json") -> KitchenConfig:
    data = _drop_comment_keys(_read_json(path))
    return KitchenConfig.model_validate(data)


def load_zone_config(path: Path | str = "data/delivery_zone.json") -> ZoneConfig:
    data = _drop_comment_keys(_read_json(path))
    polygon = tuple(tuple(point) for point in data["polygon"])
    return ZoneConfig.model_validate({**data, "polygon": polygon})
