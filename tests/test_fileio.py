"""Scrierea atomică folosită de scripturile care rescriu fișierele din `data/`.

Ce se verifică: fișierul destinație nu ajunge niciodată într-o stare intermediară,
iar o scriere eșuată nu lasă gunoi în urmă. API-ul citește `data/` la fiecare
pornire — un JSON trunchiat acolo înseamnă server care nu mai pornește.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from packages.fileio import DATA_FILE_MODE, write_json_atomic


def test_writes_readable_json_with_romanian_characters(tmp_path: Path):
    # Arrange
    target = tmp_path / "date.json"
    payload = [{"street": "Strada Ștefan cel Mare", "number": "24"}]

    # Act
    write_json_atomic(target, payload)

    # Assert
    assert json.loads(target.read_text(encoding="utf-8")) == payload
    assert "Ștefan" in target.read_text(encoding="utf-8"), "diacriticele rămân litere, nu \\u"


def test_ends_the_file_with_a_newline(tmp_path: Path):
    target = tmp_path / "date.json"

    write_json_atomic(target, {"a": 1})

    assert target.read_text(encoding="utf-8").endswith("\n")


def test_replaces_the_previous_content_completely(tmp_path: Path):
    target = tmp_path / "date.json"
    write_json_atomic(target, [{"lung": "o intrare mult mai lungă decât următoarea"}])

    write_json_atomic(target, [])

    assert json.loads(target.read_text(encoding="utf-8")) == []


def test_gives_the_file_the_permissions_the_rest_of_data_has(tmp_path: Path):
    target = tmp_path / "date.json"

    write_json_atomic(target, {"a": 1})

    assert target.stat().st_mode & 0o777 == DATA_FILE_MODE


def test_a_failed_move_leaves_no_temporary_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Arrange — discul plin sau permisiuni lipsă se manifestă exact așa: `replace` cade
    # după ce temporarul a fost deja scris.
    target = tmp_path / "date.json"

    def failing_replace(self: Path, other: Any) -> None:
        raise OSError("disc plin")

    monkeypatch.setattr(Path, "replace", failing_replace)

    # Act
    with pytest.raises(OSError, match="disc plin"):
        write_json_atomic(target, {"a": 1})

    # Assert
    assert not target.exists()
    assert list(tmp_path.iterdir()) == [], "temporarul nu are voie să rămână pe disc"


def test_a_failed_serialization_leaves_neither_target_nor_temporary(tmp_path: Path):
    # Arrange — un obiect pe care `json.dumps` nu-l poate serializa.
    target = tmp_path / "date.json"
    unserializable: Any = {"set": {1, 2, 3}}

    # Act
    with pytest.raises(TypeError):
        write_json_atomic(target, unserializable)

    # Assert
    assert not target.exists()
    assert list(tmp_path.iterdir()) == [], "temporarul nu are voie să rămână pe disc"
