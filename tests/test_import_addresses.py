"""`scripts/import_addresses.py` — importul de străzi și numere din OpenStreetMap.

Niciun test nu atinge rețeaua și niciunul nu atinge `data/`: cache-ul OSM, fixture-ul,
registrul și zona trăiesc în `tmp_path`, redirectate prin `monkeypatch`. Ce se verifică
aici e curățarea datelor brute — partea care decide dacă agentul găsește sau nu adresa
rostită de client.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.api.geocoding import AddressFixtureEntry
from scripts import import_addresses

_ZONE = {
    "polygon": [[47.630, 26.230], [47.670, 26.230], [47.670, 26.290], [47.630, 26.290]],
    "min_order_bani": 5000,
    "delivery_fee_bani": 1200,
}


def _node(
    street: str | None, number: str, lat: float, lon: float, **tags: str
) -> dict[str, object]:
    """Un element OSM de tip nod, în forma pe care o întoarce Overpass."""
    element_tags: dict[str, str] = {"addr:housenumber": number, **tags}
    if street is not None:
        element_tags["addr:street"] = street
    return {
        "type": "node",
        "id": abs(hash((street, number))) % 10**9,
        "lat": lat,
        "lon": lon,
        "tags": element_tags,
    }


def _way(name: str, lat: float, lon: float, highway: str = "residential") -> dict[str, object]:
    """Un segment de drum cu nume, cu centrul deja calculat de Overpass."""
    return {
        "type": "way",
        "id": abs(hash(name)) % 10**9,
        "center": {"lat": lat, "lon": lon},
        "tags": {"highway": highway, "name": name},
    }


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Cache, fixture, registru și zonă izolate în `tmp_path`."""
    cache_dir = tmp_path / "osm-cache"
    cache_dir.mkdir()
    zone_path = tmp_path / "delivery_zone.json"
    zone_path.write_text(json.dumps(_ZONE), encoding="utf-8")

    monkeypatch.setattr(import_addresses, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(import_addresses, "FIXTURE_PATH", tmp_path / "addresses.fixture.json")
    monkeypatch.setattr(import_addresses, "STREETS_PATH", tmp_path / "suceava.streets.json")
    monkeypatch.setattr(import_addresses, "ZONE_PATH", zone_path)
    return {
        "cache": cache_dir,
        "fixture": tmp_path / "addresses.fixture.json",
        "streets": tmp_path / "suceava.streets.json",
    }


def _write_cache(
    paths: dict[str, Path],
    addresses: list[dict[str, object]],
    streets: list[dict[str, object]] | None = None,
) -> None:
    (paths["cache"] / "addresses.json").write_text(
        json.dumps({"elements": addresses}, ensure_ascii=False), encoding="utf-8"
    )
    (paths["cache"] / "streets.json").write_text(
        json.dumps({"elements": streets or []}, ensure_ascii=False), encoding="utf-8"
    )


def _fixture(paths: dict[str, Path]) -> list[dict[str, object]]:
    return json.loads(paths["fixture"].read_text(encoding="utf-8"))


def _streets(paths: dict[str, Path]) -> list[dict[str, object]]:
    return json.loads(paths["streets"].read_text(encoding="utf-8"))["streets"]


class TestBuild:
    def test_writes_both_data_files(self, paths):
        # Arrange
        _write_cache(
            paths,
            [_node("Strada Zorilor", "12", 47.65, 26.26)],
            [_way("Strada Zorilor", 47.65, 26.26)],
        )

        # Act
        exit_code = import_addresses.main(["build"])

        # Assert
        assert exit_code == 0
        assert _fixture(paths) == [
            {"street": "Strada Zorilor", "number": "12", "lat": 47.65, "lon": 26.26,
             "confidence": 0.9, "source": "osm"}
        ]
        assert _streets(paths) == [
            {"name": "Strada Zorilor", "key": "zorilor", "lat": 47.65, "lon": 26.26,
             "address_count": 1}
        ]

    def test_dry_run_writes_nothing(self, paths):
        _write_cache(paths, [_node("Strada Zorilor", "12", 47.65, 26.26)])

        assert import_addresses.main(["build", "--dry-run"]) == 0
        assert not paths["fixture"].exists()
        assert not paths["streets"].exists()

    def test_the_same_street_written_differently_becomes_one_street(self, paths):
        # OSM ține „Ştefan cel Mare" (sedilă, fără prefix) și „Strada Ștefan cel Mare"
        # ca etichete separate — sunt aceeași stradă.
        _write_cache(
            paths,
            [
                _node("Ştefan cel Mare", "23", 47.6432, 26.2596),
                _node("Strada Ștefan cel Mare", "28", 47.6437, 26.2587),
            ],
            [_way("Strada Ștefan cel Mare", 47.6435, 26.2590)],
        )

        import_addresses.main(["build"])

        streets = _streets(paths)
        assert len(streets) == 1
        assert streets[0]["name"] == "Strada Ștefan cel Mare"
        assert {entry["street"] for entry in _fixture(paths)} == {"Strada Ștefan cel Mare"}

    def test_same_number_twice_produces_one_address(self, paths):
        # Două noduri pe același număr (clădirea și intrarea) ar deveni doi candidați,
        # iar agentul ar cere o clarificare imposibilă.
        _write_cache(
            paths,
            [
                _node("Ştefan cel Mare", "23", 47.6432, 26.2596),
                _node("Strada Ștefan cel Mare", "23", 47.6434, 26.2596),
            ],
        )

        import_addresses.main(["build"])

        assert len(_fixture(paths)) == 1

    def test_skips_elements_without_a_street(self, paths):
        _write_cache(paths, [_node(None, "12", 47.65, 26.26)])

        import_addresses.main(["build"])

        assert _fixture(paths) == []

    def test_skips_other_localities(self, paths):
        _write_cache(
            paths,
            [
                _node("Strada Zorilor", "12", 47.65, 26.26),
                _node("Strada Bisericii", "3", 47.66, 26.24, **{"addr:city": "Sfântu Ilie"}),
            ],
        )

        import_addresses.main(["build"])

        assert [entry["street"] for entry in _fixture(paths)] == ["Strada Zorilor"]

    def test_skips_entries_without_a_usable_number(self, paths):
        _write_cache(paths, [_node("Strada Zorilor", "F.N.", 47.65, 26.26)])

        import_addresses.main(["build"])

        assert _fixture(paths) == []

    def test_keeps_the_postcode_when_osm_has_one(self, paths):
        _write_cache(
            paths, [_node("Strada Zorilor", "12", 47.65, 26.26, **{"addr:postcode": "720123"})]
        )

        import_addresses.main(["build"])

        assert _fixture(paths)[0]["postcode"] == "720123"

    def test_sorts_numbers_numerically_not_alphabetically(self, paths):
        _write_cache(
            paths,
            [
                _node("Strada Zorilor", "10", 47.651, 26.261),
                _node("Strada Zorilor", "9", 47.652, 26.262),
                _node("Strada Zorilor", "2", 47.653, 26.263),
            ],
        )

        import_addresses.main(["build"])

        assert [entry["number"] for entry in _fixture(paths)] == ["2", "9", "10"]

    def test_leaves_footpaths_out_of_the_street_registry(self, paths):
        _write_cache(
            paths,
            [],
            [_way("Aleea Pietonală", 47.65, 26.26, highway="footway")],
        )

        import_addresses.main(["build"])

        assert _streets(paths) == []

    def test_reports_a_missing_cache_instead_of_crashing(self, paths, capsys):
        exit_code = import_addresses.main(["build"])

        assert exit_code == 1
        assert "fetch" in capsys.readouterr().err

    def test_reports_invalid_cache_json_instead_of_crashing(self, paths, capsys):
        (paths["cache"] / "addresses.json").write_text("{nu e json", encoding="utf-8")

        exit_code = import_addresses.main(["build"])

        assert exit_code == 1
        assert "JSON invalid" in capsys.readouterr().err


class TestCuratedAddresses:
    def test_keeps_a_manual_address_the_import_does_not_cover(self, paths):
        # Arrange — Ștefan cel Mare 24 nu există în OSM, dar e adresa demo a harness-ului.
        paths["fixture"].write_text(
            json.dumps(
                [{"street": "Strada Ștefan cel Mare", "number": "24", "lat": 47.645,
                  "lon": 26.2545, "confidence": 0.98}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        _write_cache(paths, [_node("Ştefan cel Mare", "23", 47.6432, 26.2596)])

        # Act
        import_addresses.main(["build"])

        # Assert
        assert [entry["number"] for entry in _fixture(paths)] == ["23", "24"]

    def test_import_wins_over_a_manual_address_on_the_same_number(self, paths):
        paths["fixture"].write_text(
            json.dumps(
                [{"street": "Strada Ștefan cel Mare", "number": "23", "lat": 47.60,
                  "lon": 26.20, "confidence": 0.98}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        _write_cache(paths, [_node("Ştefan cel Mare", "23", 47.6432, 26.2596)])

        import_addresses.main(["build"])

        entries = _fixture(paths)
        assert len(entries) == 1
        assert entries[0]["lat"] == 47.6432

    def test_drops_addresses_left_over_from_a_previous_import(self, paths):
        # Arrange — o adresă marcată `osm` care nu mai apare în sursă: a fost ștearsă
        # din OpenStreetMap între importuri și trebuie să dispară și din fixture.
        paths["fixture"].write_text(
            json.dumps(
                [{"street": "Strada Demolată", "number": "1", "lat": 47.65, "lon": 26.26,
                  "confidence": 0.9, "source": "osm"}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        _write_cache(paths, [_node("Strada Zorilor", "12", 47.65, 26.26)])

        # Act
        import_addresses.main(["build"])

        # Assert
        assert [entry["street"] for entry in _fixture(paths)] == ["Strada Zorilor"]

    def test_marks_imported_addresses_as_coming_from_osm(self, paths):
        _write_cache(paths, [_node("Strada Zorilor", "12", 47.65, 26.26)])

        import_addresses.main(["build"])

        assert _fixture(paths)[0]["source"] == "osm"

    def test_an_entry_without_a_source_field_is_treated_as_written_by_hand(self, paths):
        # Fixture-urile vechi n-au câmpul; a le șterge tăcut ar fi pierdere de date.
        paths["fixture"].write_text(
            json.dumps(
                [{"street": "Strada Veche", "number": "1", "lat": 47.65, "lon": 26.26,
                  "confidence": 0.98}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        _write_cache(paths, [_node("Strada Zorilor", "12", 47.65, 26.26)])

        import_addresses.main(["build"])

        assert "Strada Veche" in {entry["street"] for entry in _fixture(paths)}

    def test_refuses_to_overwrite_a_fixture_it_cannot_read(self, paths, capsys):
        paths["fixture"].write_text("{stricat", encoding="utf-8")
        _write_cache(paths, [_node("Strada Zorilor", "12", 47.65, 26.26)])

        exit_code = import_addresses.main(["build"])

        assert exit_code == 1
        assert "adrese manuale" in capsys.readouterr().err


class TestVerify:
    def test_accepts_the_files_the_import_just_wrote(self, paths):
        _write_cache(
            paths,
            [_node("Strada Zorilor", "12", 47.65, 26.26)],
            [_way("Strada Zorilor", 47.65, 26.26)],
        )
        import_addresses.main(["build"])

        assert import_addresses.main(["verify"]) == 0

    def test_reports_missing_files_instead_of_crashing(self, paths, capsys):
        assert import_addresses.main(["verify"]) == 1
        assert "Eroare" in capsys.readouterr().err

    def test_flags_addresses_whose_street_is_missing_from_the_registry(self, paths, capsys):
        # Fișierele pot fi editate și de mână; verify e plasa care prinde asta.
        paths["fixture"].write_text(
            json.dumps(
                [{"street": "Strada Zorilor", "number": "12", "lat": 47.65, "lon": 26.26,
                  "confidence": 0.9}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        paths["streets"].write_text(json.dumps({"streets": []}), encoding="utf-8")

        exit_code = import_addresses.main(["verify"])

        assert exit_code == 0
        assert "zorilor" in capsys.readouterr().out


class TestMalformedSource:
    def test_survives_an_element_whose_tags_are_not_an_object(self, paths):
        # Overpass e o sursă externă: o intrare stricată nu are voie să oprească
        # importul celorlalte câteva mii.
        _write_cache(
            paths,
            [
                {"type": "node", "id": 1, "lat": 47.65, "lon": 26.26, "tags": None},
                _node("Strada Zorilor", "12", 47.65, 26.26),
            ],
        )

        assert import_addresses.main(["build"]) == 0
        assert [entry["number"] for entry in _fixture(paths)] == ["12"]

    def test_ignores_tag_values_that_are_not_text(self, paths):
        _write_cache(
            paths,
            [{"type": "node", "id": 2, "lat": 47.65, "lon": 26.26,
              "tags": {"addr:street": 42, "addr:housenumber": 7}}],
        )

        assert import_addresses.main(["build"]) == 0
        assert _fixture(paths) == []

    def test_keeps_an_address_that_has_no_city_tag(self, paths):
        # Interogarea Overpass rulează pe conturul administrativ al municipiului,
        # deci lipsa etichetei nu înseamnă „altă localitate".
        _write_cache(paths, [_node("Strada Zorilor", "12", 47.65, 26.26)])

        import_addresses.main(["build"])

        assert [entry["street"] for entry in _fixture(paths)] == ["Strada Zorilor"]


class TestBetterDuplicate:
    def test_prefers_the_more_confident_entry(self):
        node = AddressFixtureEntry(
            street="Strada Zorilor", number="12", lat=47.65, lon=26.26, confidence=0.90
        )
        area = AddressFixtureEntry(
            street="Strada Zorilor", number="12", lat=47.65, lon=26.26, confidence=0.85
        )

        assert import_addresses._better_duplicate(area, node) is node

    def test_prefers_the_entry_with_a_postcode_when_confidence_ties(self):
        plain = AddressFixtureEntry(
            street="Strada Zorilor", number="12", lat=47.65, lon=26.26, confidence=0.90
        )
        with_postcode = plain.model_copy(update={"postcode": "720123"})

        assert import_addresses._better_duplicate(plain, with_postcode) is with_postcode

    def test_picks_the_same_entry_regardless_of_the_order_overpass_returned_them(self):
        # Fără o departajare finală, alegerea ar cădea pe ordinea de răspuns a
        # serviciului, iar fiecare re-import ar da un diff fără schimbare reală.
        north = AddressFixtureEntry(
            street="Strada Zorilor", number="12", lat=47.66, lon=26.26, confidence=0.90
        )
        south = AddressFixtureEntry(
            street="Strada Zorilor", number="12", lat=47.65, lon=26.26, confidence=0.90
        )

        assert import_addresses._better_duplicate(north, south) is (
            import_addresses._better_duplicate(south, north)
        )


class TestInvalidSourceData:
    def test_an_absurd_street_name_is_skipped_not_fatal(self, paths):
        # Plafoanele din model sunt ultima barieră în fața etichetelor OSM. O intrare
        # care le încalcă nu are voie să oprească importul celorlalte câteva mii.
        _write_cache(
            paths,
            [
                _node("S" * 500, "1", 47.65, 26.26),
                _node("Strada Zorilor", "12", 47.65, 26.26),
            ],
        )

        assert import_addresses.main(["build"]) == 0
        assert [entry["street"] for entry in _fixture(paths)] == ["Strada Zorilor"]

    def test_an_impossible_coordinate_is_skipped(self, paths):
        _write_cache(
            paths,
            [
                _node("Strada Imposibilă", "1", 999.0, 26.26),
                _node("Strada Zorilor", "12", 47.65, 26.26),
            ],
        )

        assert import_addresses.main(["build"]) == 0
        assert [entry["street"] for entry in _fixture(paths)] == ["Strada Zorilor"]

    def test_the_report_counts_the_skipped_entries(self, paths, capsys):
        _write_cache(paths, [_node("S" * 500, "1", 47.65, 26.26)])

        import_addresses.main(["build"])

        assert "dată invalidă" in capsys.readouterr().out
