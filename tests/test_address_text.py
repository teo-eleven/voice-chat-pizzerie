"""Teste pentru canonicalizarea numelor de stradă și a numerelor de casă."""

from __future__ import annotations

import pytest

from packages.domain.address_text import (
    canonical_street,
    house_number_key,
    parse_house_number,
    preferred_street_name,
    split_street_type,
    street_key,
)


class TestSplitStreetType:
    def test_recognizes_full_prefix(self):
        assert split_street_type("Strada Zorilor") == ("Strada", "Zorilor")

    def test_recognizes_abbreviation_with_dot(self):
        assert split_street_type("Str. Zorilor") == ("Strada", "Zorilor")

    def test_recognizes_hyphenated_abbreviation(self):
        assert split_street_type("B-dul George Enescu") == ("Bulevardul", "George Enescu")

    def test_returns_none_when_name_has_no_prefix(self):
        assert split_street_type("22 Decembrie") == (None, "22 Decembrie")

    def test_does_not_consume_the_whole_name_as_prefix(self):
        # „Calea" singură e numele întreg, nu un prefix cu rest gol.
        assert split_street_type("Calea") == (None, "Calea")

    def test_returns_empty_for_blank_name(self):
        assert split_street_type("   ") == (None, "")


class TestStreetKey:
    @pytest.mark.parametrize(
        "name",
        [
            "Strada Ștefan cel Mare",  # diacritice cu virgulă
            "Ştefan cel Mare",  # sedilă, fără prefix — forma din OSM
            "str. stefan cel mare",  # rostit și transcris fără diacritice
            "  STRADA  ȘTEFAN   CEL MARE ",
        ],
    )
    def test_all_spellings_of_a_street_share_one_key(self, name):
        assert street_key(name) == "stefan cel mare"

    def test_different_streets_have_different_keys(self):
        assert street_key("Strada Mihai Viteazu") != street_key("Strada Mihai Eminescu")

    def test_name_without_prefix_keeps_its_words(self):
        assert street_key("22 Decembrie") == "22 decembrie"


class TestCanonicalStreet:
    def test_expands_abbreviated_prefix(self):
        assert canonical_street("Str. Zorilor") == "Strada Zorilor"

    def test_adds_default_prefix_when_missing(self):
        assert canonical_street("22 Decembrie") == "Strada 22 Decembrie"

    def test_keeps_non_default_prefix(self):
        assert canonical_street("Calea Unirii") == "Calea Unirii"

    def test_collapses_extra_whitespace(self):
        assert canonical_street("Strada   Ana   Ipătescu") == "Strada Ana Ipătescu"

    def test_does_not_duplicate_an_existing_prefix(self):
        assert canonical_street("Strada Zorilor") == "Strada Zorilor"

    def test_returns_empty_for_a_blank_name_instead_of_a_bare_prefix(self):
        # Fără asta, un nume gol ar deveni „Strada", care arată ca o stradă reală.
        assert canonical_street("   ") == ""


class TestPreferredStreetName:
    def test_prefers_the_variant_with_an_explicit_prefix(self):
        # Arrange
        variants = ["Ştefan cel Mare", "Strada Ștefan cel Mare"]

        # Act
        chosen = preferred_street_name(variants)

        # Assert
        assert chosen == "Strada Ștefan cel Mare"

    def test_prefers_proper_diacritics_over_cedilla(self):
        assert preferred_street_name(["Strada Mirăuţilor", "Strada Mirăuților"]) == (
            "Strada Mirăuților"
        )

    def test_is_stable_regardless_of_input_order(self):
        variants = ["Ştefan cel Mare", "Strada Ștefan cel Mare", "stefan cel mare"]
        assert preferred_street_name(variants) == preferred_street_name(list(reversed(variants)))

    def test_normalizes_the_single_variant_it_gets(self):
        assert preferred_street_name(["22 Decembrie"]) == "Strada 22 Decembrie"

    def test_rejects_an_empty_list_of_variants(self):
        with pytest.raises(ValueError, match="cel puțin un nume"):
            preferred_street_name([])


class TestParseHouseNumber:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("12", "12"),
            ("12a", "12A"),
            ("1BIS", "1BIS"),
            ("64SCA", "64SCA"),
            ("nr 140", "140"),
            ("4-6", "4"),  # intervalul păstrează numărul de început
            ("38/2", "38"),
            ("1 T 49", "1"),
            ("20 J C", "20"),
            ("007", "7"),  # zerourile din față nu fac parte din număr
        ],
    )
    def test_cleans_messy_source_values(self, raw, expected):
        assert parse_house_number(raw) == expected

    @pytest.mark.parametrize("raw", ["F.N.", "fara numar", "", "   "])
    def test_returns_none_when_there_is_no_number(self, raw):
        assert parse_house_number(raw) is None

    @pytest.mark.parametrize("raw", ["12345", "999999"])
    def test_rejects_an_impossibly_long_number_instead_of_truncating_it(self, raw):
        # Trunchierea tăcută („12345" -> „1234") ar scrie în date o adresă care
        # există, dar nu e cea din sursă — mai rău decât o intrare refuzată.
        assert parse_house_number(raw) is None


class TestHouseNumberKey:
    @pytest.mark.parametrize("raw", ["12A", "12 a", "012a", " 12A "])
    def test_same_number_written_differently_shares_one_key(self, raw):
        assert house_number_key(raw) == "12a"

    def test_number_and_number_with_letter_are_different(self):
        assert house_number_key("12") != house_number_key("12A")

    def test_falls_back_to_normalized_text_for_unexpected_values(self):
        assert house_number_key("F.N.") == "f.n."
