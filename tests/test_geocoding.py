"""Geocodarea offline: din ce rostește clientul, în ce e scris în fixture.

Contractul verificat: aceeași adresă găsită indiferent cum e scrisă strada (cu sau
fără „Strada", cu diacritice corecte, cu sedilă sau fără niciunele) — asta e tot ce
stă între o transcriere STT imperfectă și un „nu am găsit adresa".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.api.geocoding import (
    AddressFixtureEntry,
    AddressIndex,
    geocode,
    load_fixture,
    load_index,
    load_streets,
    parse_spoken_address,
)

_ENTRIES = (
    AddressFixtureEntry(
        street="Strada Ștefan cel Mare", number="24", lat=47.645, lon=26.2545, confidence=0.98
    ),
    AddressFixtureEntry(
        street="Strada Zorilor", number="12A", lat=47.65, lon=26.26, confidence=0.9
    ),
    AddressFixtureEntry(
        street="Calea Unirii", number="15", lat=47.657, lon=26.25, confidence=0.95
    ),
)


@pytest.fixture
def index() -> AddressIndex:
    return AddressIndex.build(_ENTRIES)


class TestParseSpokenAddress:
    def test_splits_street_from_number(self):
        assert parse_spoken_address("Strada Ștefan cel Mare 24") == {
            "street": "Strada Ștefan cel Mare",
            "number": "24",
        }

    def test_extracts_delivery_details(self):
        parsed = parse_spoken_address(
            "Strada Ștefan cel Mare 24, bloc 12, scara B, etaj 3, apartament 47, interfon 47"
        )

        assert parsed["block"] == "12"
        assert parsed["staircase"] == "B"
        assert parsed["floor"] == "3"
        assert parsed["apartment"] == "47"
        assert parsed["intercom"] == "47"

    def test_keeps_a_street_name_that_ends_in_digits(self):
        parsed = parse_spoken_address("Bulevardul 1 Decembrie 1918 8")

        assert parsed == {"street": "Bulevardul 1 Decembrie 1918", "number": "8"}

    @pytest.mark.parametrize(
        ("spoken", "expected_number"),
        [
            ("Strada Prunului 1BIS", "1BIS"),
            ("Strada Prunului 1 bis", "1 bis"),
            ("Strada Mărășești 64SCA", "64SCA"),
            ("Strada Mărășești 64 SCA", "64 SCA"),
        ],
    )
    def test_reads_a_multi_letter_number_suffix(self, spoken, expected_number):
        # Suceava chiar are astfel de numere („64SCA".."64SCJ", „1BIS"): un sufix
        # de o singură literă le-ar lăsa negăsibile prin voce.
        assert parse_spoken_address(spoken)["number"] == expected_number


class TestGeocode:
    @pytest.mark.parametrize(
        "spoken",
        [
            "Strada Ștefan cel Mare 24",
            "Ștefan cel Mare 24",  # clientul sare peste „Strada"
            "Ştefan cel Mare 24",  # sedilă, cum vine din OSM
            "stefan cel mare 24",  # STT fără diacritice
            "Str. Ștefan cel Mare 24",
        ],
    )
    def test_finds_the_address_however_the_street_is_written(self, index, spoken):
        candidates = geocode(spoken, index)

        assert len(candidates) == 1
        assert candidates[0].address.street == "Strada Ștefan cel Mare"

    def test_matches_a_number_with_a_letter_written_apart(self):
        candidates = geocode("Zorilor 12 a", index=AddressIndex.build(_ENTRIES))

        assert len(candidates) == 1
        assert candidates[0].address.number == "12A"

    def test_carries_the_delivery_details_into_the_candidate(self):
        candidates = geocode(
            "Strada Zorilor 12A, bloc 3, scara B, apartament 47",
            AddressIndex.build(_ENTRIES),
        )

        address = candidates[0].address
        assert (address.block, address.staircase, address.apartment) == ("3", "B", "47")
        assert address.formatted == "Strada Zorilor 12A, bloc 3, scara B, apartament 47"

    @pytest.mark.parametrize("spoken", ["Strada Prunului 1BIS", "Strada Prunului 1 bis"])
    def test_finds_an_address_whose_number_has_a_multi_letter_suffix(self, spoken):
        entries = (
            AddressFixtureEntry(
                street="Strada Prunului", number="1BIS", lat=47.65, lon=26.26, confidence=0.9
            ),
        )

        candidates = geocode(spoken, AddressIndex.build(entries))

        assert len(candidates) == 1
        assert candidates[0].address.number == "1BIS"

    def test_returns_nothing_for_an_unknown_street(self, index):
        assert geocode("Strada Inexistentă 5", index) == ()

    def test_returns_nothing_for_a_known_street_with_an_unknown_number(self, index):
        assert geocode("Strada Zorilor 999", index) == ()

    def test_returns_nothing_when_the_client_gave_no_number(self, index):
        # Fără număr n-avem ce livra; agentul trebuie să întrebe, nu să ghicească.
        assert geocode("Strada Zorilor", index) == ()

    def test_marks_candidates_out_of_zone_until_the_zone_check_runs(self, index):
        # `in_zone` e decis de `delivery_zone`, nu aici — placeholder-ul e mereu False.
        assert all(not candidate.in_zone for candidate in geocode("Calea Unirii 15", index))


class TestAddressIndex:
    def test_groups_two_entries_on_the_same_street_and_number(self):
        ambiguous = (
            AddressFixtureEntry(
                street="Strada Mihai Viteazu", number="12", lat=47.6455, lon=26.257,
                confidence=0.9
            ),
            AddressFixtureEntry(
                street="Mihai Viteazu", number="12", lat=47.6462, lon=26.2588, confidence=0.85
            ),
        )

        assert len(AddressIndex.build(ambiguous).lookup("Strada Mihai Viteazu", "12")) == 2

    def test_lookup_of_a_missing_key_returns_empty(self, index):
        assert index.lookup("Strada Inexistentă", "1") == ()

    def test_keeps_every_entry_it_was_built_from(self, index):
        assert index.entries == _ENTRIES


class TestLoadFromDisk:
    def test_reads_the_real_project_fixture(self):
        entries = load_fixture()

        assert len(entries) > 1000, "fixture-ul importat trebuie să acopere municipiul"
        assert all(entry.city == "Suceava" for entry in entries)
        assert all(entry.county == "Suceava" for entry in entries)
        assert all(entry.country == "România" for entry in entries)

    def test_reads_the_real_street_registry(self):
        registry = load_streets()

        assert registry.city == "Suceava"
        assert registry.county == "Suceava"
        assert registry.country == "România"
        assert len(registry.streets) > 100

    def test_the_demo_address_of_the_harness_still_resolves(self):
        # `apps/web/harness.html` comandă la Ștefan cel Mare 24; dacă importul o pierde,
        # demo-ul din browser se rupe fără ca vreun test de API să observe.
        candidates = geocode("Strada Stefan cel Mare 24, bloc 12, scara B", load_index())

        assert len(candidates) == 1

    def test_rejects_a_fixture_with_a_broken_entry(self, tmp_path: Path):
        broken = tmp_path / "addresses.fixture.json"
        broken.write_text(json.dumps([{"street": "Strada Zorilor"}]), encoding="utf-8")

        with pytest.raises(ValueError):
            load_fixture(broken)
