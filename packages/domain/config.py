"""Încărcare configurație de pe disc: capacitatea bucătăriei și zona de livrare.

Alături de `catalog.py`, singurele module care fac I/O în domeniu.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import KitchenConfig, ZoneConfig


def _read_json(path: Path | str) -> dict[str, Any]:
    """Citește JSON-ul și confirmă că e un obiect, nu o listă sau un scalar.

    `json.loads` întoarce `Any`, adică orice formă de fișier ar trece mai departe
    nevăzută până când `model_validate` ar da o eroare fără nume de fișier în ea.
    Verificarea de aici e granița dintre disc și domeniu — un fișier de configurare
    stricat trebuie să spună care fișier e stricat.
    """
    data: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Fișierul de configurare „{path}” nu conține un obiect JSON.")
    return data


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
