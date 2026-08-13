"""`scripts/add_address.py` — unealta cu care se adaugă străzi în fixture-ul offline.

Scriptul scrie pe disc, deci fiecare test își primește propriul fixture și propria
zonă în `tmp_path`, cu `FIXTURE_PATH`/`ZONE_PATH` redirectate prin `monkeypatch`.
Fișierele reale din `data/` nu sunt atinse de nicio linie de aici.

Contractul verificat peste tot: o eroare de utilizator iese cu cod 1 și un mesaj
citibil pe stderr, niciodată cu traceback. Scriptul e folosit direct din terminal de
proprietarul pizzeriei, nu de un programator.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import add_address

#: Poligon pătrat peste centrul Suceava, suficient pentru a separa „în zonă" de „afară".
_ZONE = {
    "polygon": [[47.630, 26.230], [47.670, 26.230], [47.670, 26.290], [47.630, 26.290]],
    "min_order_bani": 5000,
    "delivery_fee_bani": 1200,
}
_IN_ZONE = (47.650, 26.260)
_OUTSIDE = (47.700, 26.400)


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Fixture și zonă izolate, cu o singură adresă de pornire."""
    fixture_path = tmp_path / "addresses.fixture.json"
    zone_path = tmp_path / "delivery_zone.json"
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "street": "Strada Ștefan cel Mare",
                    "number": "24",
                    "lat": _IN_ZONE[0],
                    "lon": _IN_ZONE[1],
                    "confidence": 0.98,
                    "city": "Suceava",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    zone_path.write_text(json.dumps(_ZONE), encoding="utf-8")
    monkeypatch.setattr(add_address, "FIXTURE_PATH", fixture_path)
    monkeypatch.setattr(add_address, "ZONE_PATH", zone_path)
    return {"fixture": fixture_path, "zone": zone_path}


def _entries(paths: dict[str, Path]) -> list[dict[str, object]]:
    return json.loads(paths["fixture"].read_text(encoding="utf-8"))


class TestAddSucceeds:
    def test_adds_an_address_inside_the_zone(self, paths, capsys):
        # Act
        code = add_address.main(
            ["add", "--street", "Strada Zorilor", "--number", "12",
             "--lat", str(_IN_ZONE[0]), "--lon", str(_IN_ZONE[1])]
        )

        # Assert
        assert code == 0
        assert "ÎN ZONA de livrare" in capsys.readouterr().out
        streets = [entry["street"] for entry in _entries(paths)]
        assert streets == ["Strada Ștefan cel Mare", "Strada Zorilor"]

    def test_adds_an_address_outside_the_zone_but_warns(self, paths, capsys):
        # Act
        code = add_address.main(
            ["add", "--street", "Strada Departe", "--number", "1",
             "--lat", str(_OUTSIDE[0]), "--lon", str(_OUTSIDE[1])]
        )

        # Assert: se adaugă (poate fi intenționat, pentru testarea refuzului), dar spune
        assert code == 0
        out = capsys.readouterr().out
        assert "ÎN AFARA zonei" in out
        assert "ATENȚIE" in out
        assert len(_entries(paths)) == 2

    def test_written_file_keeps_diacritics_readable(self, paths):
        # Act
        add_address.main(
            ["add", "--street", "Strada Mărășești", "--number", "7",
             "--lat", str(_IN_ZONE[0]), "--lon", "26.261"]
        )

        # Assert: scris cu `ensure_ascii=False`, nu cu secvențe \u
        raw = paths["fixture"].read_text(encoding="utf-8")
        assert "Mărășești" in raw


class TestAddRefuses:
    def test_refuses_an_exact_duplicate_ignoring_diacritics(self, paths, capsys):
        # Arrange / Act: „Stefan" fără diacritice, aceeași stradă normalizată
        code = add_address.main(
            ["add", "--street", "Strada Stefan cel Mare", "--number", "24",
             "--lat", str(_IN_ZONE[0]), "--lon", str(_IN_ZONE[1])]
        )

        # Assert
        assert code == 1
        assert "există deja" in capsys.readouterr().err
        assert len(_entries(paths)) == 1

    @pytest.mark.parametrize(
        ("lat", "lon", "expected"),
        [("200", "26.26", "Latitudinea"), ("47.65", "999", "Longitudinea")],
    )
    def test_refuses_impossible_coordinates(self, paths, capsys, lat, lon, expected):
        # Act
        code = add_address.main(
            ["add", "--street", "Strada X", "--number", "1", "--lat", lat, "--lon", lon]
        )

        # Assert: refuzat înainte de orice scriere pe disc
        assert code == 1
        assert expected in capsys.readouterr().err
        assert len(_entries(paths)) == 1


class TestBrokenFilesFailCleanly:
    """Fiecare fișier stricat trebuie să dea mesaj, nu traceback."""

    def test_missing_fixture(self, paths, capsys):
        # Arrange
        paths["fixture"].unlink()

        # Act / Assert
        assert add_address.main(["list"]) == 1
        assert "nu a fost găsit" in capsys.readouterr().err

    def test_missing_zone(self, paths, capsys):
        # Arrange
        paths["zone"].unlink()

        # Act / Assert
        assert add_address.main(["list"]) == 1
        assert "nu a fost găsit" in capsys.readouterr().err

    def test_invalid_json_in_fixture(self, paths, capsys):
        # Arrange
        paths["fixture"].write_text("{ nu e json", encoding="utf-8")

        # Act / Assert
        assert add_address.main(["list"]) == 1
        assert "JSON invalid" in capsys.readouterr().err

    def test_polygon_with_fewer_than_three_points(self, paths, capsys):
        # Arrange: `point_in_polygon` refuză asta ridicând `DomainError`, care nu
        # descinde din `AddressScriptError` — exact cazul care dădea traceback brut.
        paths["zone"].write_text(
            json.dumps({**_ZONE, "polygon": _ZONE["polygon"][:2]}), encoding="utf-8"
        )

        # Act / Assert
        assert add_address.main(["list"]) == 1
        assert "cel puțin 3 puncte" in capsys.readouterr().err


class TestListAndCheck:
    def test_list_counts_addresses_by_zone(self, paths, capsys):
        # Arrange
        add_address.main(
            ["add", "--street", "Strada Departe", "--number", "1",
             "--lat", str(_OUTSIDE[0]), "--lon", str(_OUTSIDE[1])]
        )
        capsys.readouterr()

        # Act
        code = add_address.main(["list"])

        # Assert
        assert code == 0
        assert "Total: 2 adrese — 1 în zonă, 1 în afara zonei." in capsys.readouterr().out

    def test_list_on_empty_fixture(self, paths, capsys):
        # Arrange
        paths["fixture"].write_text("[]", encoding="utf-8")

        # Act / Assert
        assert add_address.main(["list"]) == 0
        assert "este gol" in capsys.readouterr().out

    def test_check_reports_both_verdicts_without_writing(self, paths, capsys):
        # Arrange
        before = paths["fixture"].read_text(encoding="utf-8")

        # Act
        inside_args = ["check", "--lat", str(_IN_ZONE[0]), "--lon", str(_IN_ZONE[1])]
        assert add_address.main(inside_args) == 0
        inside = capsys.readouterr().out
        outside_args = ["check", "--lat", str(_OUTSIDE[0]), "--lon", str(_OUTSIDE[1])]
        assert add_address.main(outside_args) == 0
        outside = capsys.readouterr().out

        # Assert
        assert "este ÎN zona" in inside
        assert "ÎN AFARA zonei" in outside
        assert paths["fixture"].read_text(encoding="utf-8") == before
