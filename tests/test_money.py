"""Teste unitare pentru packages/domain/money.py."""

from __future__ import annotations

from packages.domain.money import format_ron, lei_to_bani, spoken_ron, sum_bani


class TestLeiToBani:
    def test_converts_dot_decimal_string_to_bani(self):
        # Arrange
        lei = "34.90"

        # Act
        result = lei_to_bani(lei)

        # Assert
        assert result == 3490

    def test_converts_comma_decimal_string_to_bani(self):
        # Arrange
        lei = "34,90"

        # Act
        result = lei_to_bani(lei)

        # Assert
        assert result == 3490

    def test_converts_whole_lei_string_without_decimals(self):
        # Arrange
        lei = "45"

        # Act
        result = lei_to_bani(lei)

        # Assert
        assert result == 4500

    def test_converts_int_input_as_whole_lei(self):
        # Arrange
        lei = 45

        # Act
        result = lei_to_bani(lei)

        # Assert
        assert result == 4500

    def test_pads_single_digit_fraction_to_two_digits(self):
        # Arrange
        lei = "34.9"

        # Act
        result = lei_to_bani(lei)

        # Assert
        assert result == 3490

    def test_strips_surrounding_whitespace(self):
        # Arrange
        lei = "  34.90  "

        # Act
        result = lei_to_bani(lei)

        # Assert
        assert result == 3490

    def test_handles_negative_amount(self):
        # Arrange
        lei = "-12.50"

        # Act
        result = lei_to_bani(lei)

        # Assert
        assert result == -1250

    def test_handles_zero_whole_part_with_fraction(self):
        # Arrange
        lei = "0.50"

        # Act
        result = lei_to_bani(lei)

        # Assert
        assert result == 50


class TestFormatRon:
    def test_formats_bani_with_comma_separator_and_lei_suffix(self):
        # Arrange
        bani = 3490

        # Act
        result = format_ron(bani)

        # Assert
        assert result == "34,90 lei"

    def test_formats_zero_bani(self):
        # Arrange
        bani = 0

        # Act
        result = format_ron(bani)

        # Assert
        assert result == "0,00 lei"

    def test_pads_fraction_below_ten_bani_with_leading_zero(self):
        # Arrange
        bani = 5

        # Act
        result = format_ron(bani)

        # Assert
        assert result == "0,05 lei"

    def test_formats_negative_bani_with_leading_minus(self):
        # Arrange
        bani = -3490

        # Act
        result = format_ron(bani)

        # Assert
        assert result == "-34,90 lei"


class TestSpokenRon:
    def test_speaks_whole_and_fractional_bani_when_fraction_nonzero(self):
        # Arrange
        bani = 3490

        # Act
        result = spoken_ron(bani)

        # Assert
        assert result == "34 de lei și 90 de bani"

    def test_speaks_only_lei_when_fraction_is_zero(self):
        # Arrange
        bani = 3400

        # Act
        result = spoken_ron(bani)

        # Assert
        assert result == "34 de lei"

    def test_speaks_zero_lei_when_amount_is_zero(self):
        # Arrange
        bani = 0

        # Act
        result = spoken_ron(bani)

        # Assert
        assert result == "0 lei"

    def test_speaks_single_ban_with_correct_wording(self):
        # Arrange: 1 ban singur, fara lei
        bani = 1

        # Act
        result = spoken_ron(bani)

        # Assert: "1 ban" la singular, nu "1 de bani"
        assert result == "0 lei și 1 ban"

    def test_speaks_single_leu_at_exactly_one_hundred_bani(self):
        # Arrange: 100 bani = 1 leu exact, fara fractiune
        bani = 100

        # Act
        result = spoken_ron(bani)

        # Assert: singular "leu", nu "lei"
        assert result == "1 leu"

    def test_speaks_single_leu_and_single_ban(self):
        # Arrange: 101 bani = 1 leu si 1 ban
        bani = 101

        # Act
        result = spoken_ron(bani)

        # Assert: acord singular pe ambele substantive
        assert result == "1 leu și 1 ban"

    def test_inserts_de_for_whole_lei_above_nineteen_ending_in_zero(self):
        # Arrange: 4500 bani = 45 lei, fara fractiune
        bani = 4500

        # Act
        result = spoken_ron(bani)

        # Assert: 45 >= 20 si 45 % 100 nu e intre 1 si 19 -> "de"
        assert result == "45 de lei"

    def test_inserts_de_for_both_lei_and_bani_above_nineteen(self):
        # Arrange: 3490 bani = 34 lei si 90 de bani
        bani = 3490

        # Act
        result = spoken_ron(bani)

        # Assert: ambele componente cer particula "de"
        assert result == "34 de lei și 90 de bani"

    def test_inserts_de_for_hundreds_ending_in_ninety_nine(self):
        # Arrange: 19900 bani = 199 lei
        bani = 19900

        # Act
        result = spoken_ron(bani)

        # Assert: 199 % 100 = 99, nu e intre 1 si 19 -> "de"
        assert result == "199 de lei"

    def test_omits_de_for_two_lei_below_twenty(self):
        # Arrange: 200 bani = 2 lei
        bani = 200

        # Act
        result = spoken_ron(bani)

        # Assert: 2 < 20 -> fara "de"
        assert result == "2 lei"

    def test_omits_de_when_last_two_digits_are_between_one_and_nineteen(self):
        # Arrange: 10100 bani = 101 lei (101 % 100 = 1)
        bani = 10100

        # Act
        result = spoken_ron(bani)

        # Assert: ultimele doua cifre in intervalul 1-19 -> fara "de"
        assert result == "101 lei"

    def test_omits_de_when_last_two_digits_are_fifteen(self):
        # Arrange: 11500 bani = 115 lei (115 % 100 = 15)
        bani = 11500

        # Act
        result = spoken_ron(bani)

        # Assert: 15 e in intervalul 1-19 -> fara "de"
        assert result == "115 lei"

    def test_omits_de_for_nineteen_lei_below_twenty(self):
        # Arrange: 1900 bani = 19 lei exact, fara fractiune
        bani = 1900

        # Act
        result = spoken_ron(bani)

        # Assert: 19 < 20 -> fara "de"
        assert result == "19 lei"


class TestSumBani:
    def test_sums_iterable_of_ints_as_int(self):
        # Arrange
        values = [100, 200, 300]

        # Act
        result = sum_bani(values)

        # Assert
        assert result == 600
        assert isinstance(result, int)

    def test_returns_zero_for_empty_iterable(self):
        # Arrange
        values: list[int] = []

        # Act
        result = sum_bani(values)

        # Assert
        assert result == 0

    def test_sums_generator_expression(self):
        # Arrange
        values = (n for n in (10, 20, 30))

        # Act
        result = sum_bani(values)

        # Assert
        assert result == 60
