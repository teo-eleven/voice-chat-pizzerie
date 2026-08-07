"""Teste unitare pentru packages/domain/text.py."""

from __future__ import annotations

from packages.domain.text import normalize, tokens


class TestNormalize:
    def test_lowercases_text(self):
        # Arrange
        text_input = "PIZZA Capricciosa"

        # Act
        result = normalize(text_input)

        # Assert
        assert result == "pizza capricciosa"

    def test_strips_standard_romanian_diacritics(self):
        # Arrange
        text_input = "ĂÂÎȘȚ ăâîșț"

        # Act
        result = normalize(text_input)

        # Assert
        assert result == "aaist aaist"

    def test_strips_cedilla_variants_of_s_and_t(self):
        # Arrange: Ş/ş = S/s cu sedilă, Ţ/ţ = T/t cu sedilă
        text_input = "Ş Ţ ş ţ"

        # Act
        result = normalize(text_input)

        # Assert
        assert result == "s t s t"

    def test_collapses_multiple_whitespace_into_single_space(self):
        # Arrange
        text_input = "  vreau   o   pizza  "

        # Act
        result = normalize(text_input)

        # Assert
        assert result == "vreau o pizza"

    def test_returns_empty_string_for_empty_input(self):
        # Arrange
        text_input = ""

        # Act
        result = normalize(text_input)

        # Assert
        assert result == ""


class TestTokens:
    def test_splits_normalized_text_on_non_alphanumeric_characters(self):
        # Arrange
        text_input = "Vreau o Căpricioasă, fără ciuperci!"

        # Act
        result = tokens(text_input)

        # Assert
        assert result == ("vreau", "o", "capricioasa", "fara", "ciuperci")

    def test_returns_empty_tuple_for_empty_string(self):
        # Arrange
        text_input = ""

        # Act
        result = tokens(text_input)

        # Assert
        assert result == ()

    def test_returns_empty_tuple_for_only_punctuation(self):
        # Arrange
        text_input = "!!! ,,, ---"

        # Act
        result = tokens(text_input)

        # Assert
        assert result == ()

    def test_ignores_extra_whitespace_between_words(self):
        # Arrange
        text_input = "cola   mica"

        # Act
        result = tokens(text_input)

        # Assert
        assert result == ("cola", "mica")
