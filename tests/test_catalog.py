"""Teste unitare pentru packages/domain/catalog.py.

Foloseste fisierul real `data/menu.seed.json` (citire, permisa) pentru a verifica
incarcarea si cautarea in catalog asa cum se comporta in productie.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.domain.catalog import Catalog, load_catalog
from packages.domain.enums import Category
from packages.domain.errors import DomainError
from packages.domain.models import Product, SizeOption

_MENU_PATH = Path(__file__).resolve().parents[1] / "data" / "menu.seed.json"


@pytest.fixture
def catalog() -> Catalog:
    return load_catalog(_MENU_PATH)


class TestLoadCatalog:
    def test_produces_23_products(self, catalog: Catalog):
        # Arrange (catalog incarcat din fixture)

        # Act
        product_count = len(catalog.products)

        # Assert
        assert product_count == 23

    def test_pizza_has_three_sizes_with_correct_bani_prices(self, catalog: Catalog):
        # Arrange
        product_id = "PZ-002"

        # Act
        product = catalog.by_id(product_id)

        # Assert
        assert product is not None
        assert len(product.sizes) == 3
        prices_by_code = {size.code.value: size.price_bani for size in product.sizes}
        assert prices_by_code == {"small": 3400, "medium": 4500, "large": 5600}

    def test_non_pizza_product_has_price_bani_and_empty_sizes(self, catalog: Catalog):
        # Arrange
        product_id = "BT-001"

        # Act
        product = catalog.by_id(product_id)

        # Assert
        assert product is not None
        assert product.price_bani == 800
        assert product.sizes == ()

    def test_raises_pizza_prices_missing_when_pizza_has_no_prices_key(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # Arrange: fara scriere pe disc - simulam continutul fisierului prin monkeypatch
        fake_json = (
            '{"size_template": [{"code": "small", "label": "mica", "diameter_cm": 25, '
            '"oven_slots": 1, "bake_minutes": 7}], '
            '"products": [{"id": "PZ-X", "name": "Fara Preturi", "category": "pizza"}]}'
        )
        monkeypatch.setattr(Path, "read_text", lambda self, encoding="utf-8": fake_json)

        # Act
        with pytest.raises(DomainError) as exc:
            load_catalog("fake/menu.json")

        # Assert
        assert exc.value.issue.code == "pizza_prices_missing"

    def test_raises_pizza_size_missing_when_a_size_code_has_no_price(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # Arrange: fara scriere pe disc - simulam continutul fisierului prin monkeypatch
        fake_json = (
            '{"size_template": ['
            '{"code": "small", "label": "mica", "diameter_cm": 25, '
            '"oven_slots": 1, "bake_minutes": 7},'
            '{"code": "large", "label": "mare", "diameter_cm": 40, '
            '"oven_slots": 3, "bake_minutes": 10}'
            '], '
            '"products": [{"id": "PZ-X", "name": "Partial", "category": "pizza", '
            '"prices": {"small": "20.00"}}]}'
        )
        monkeypatch.setattr(Path, "read_text", lambda self, encoding="utf-8": fake_json)

        # Act
        with pytest.raises(DomainError) as exc:
            load_catalog("fake/menu.json")

        # Assert
        assert exc.value.issue.code == "pizza_size_missing"

    def test_raises_product_price_missing_when_non_pizza_has_no_price(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # Arrange: fara scriere pe disc - simulam continutul fisierului prin monkeypatch
        fake_json = (
            '{"size_template": [], '
            '"products": [{"id": "SO-X", "name": "Sos fara pret", "category": "sauce"}]}'
        )
        monkeypatch.setattr(Path, "read_text", lambda self, encoding="utf-8": fake_json)

        # Act
        with pytest.raises(DomainError) as exc:
            load_catalog("fake/menu.json")

        # Assert
        assert exc.value.issue.code == "product_price_missing"


class TestSearch:
    @pytest.mark.parametrize(
        "query",
        ["capriciosa", "CAPRICIOZA", "vreau o capricioasa"],
    )
    def test_finds_capricciosa_from_various_spellings(self, catalog: Catalog, query: str):
        # Arrange (catalog din fixture)

        # Act
        results = catalog.search(query)

        # Assert
        assert any(product.id == "PZ-002" for product in results)

    def test_cola_search_does_not_return_lava_cake(self, catalog: Catalog):
        """Regresie: 'cola' este substring in 'ciocolata', dar nu trebuie sa dea fals-pozitiv."""
        # Arrange
        query = "cola"

        # Act
        results = catalog.search(query)

        # Assert
        assert all(product.id != "DS-003" for product in results)
        assert any(product.id == "BT-001" for product in results)

    def test_returns_empty_tuple_for_blank_query(self, catalog: Catalog):
        # Arrange
        query = "   "

        # Act
        results = catalog.search(query)

        # Assert
        assert results == ()

    def test_returns_empty_tuple_when_nothing_matches(self, catalog: Catalog):
        # Arrange
        query = "xyzabc_nu_exista"

        # Act
        results = catalog.search(query)

        # Assert
        assert results == ()

    def test_exact_name_match_ranks_before_alias_match(self):
        # Arrange
        exact = Product(id="A", name="Margherita", category=Category.DRINK, price_bani=100)
        alias_only = Product(
            id="B", name="Alta", category=Category.DRINK, price_bani=100, aliases=("margherita",)
        )
        catalog = Catalog(products=(alias_only, exact))

        # Act
        results = catalog.search("margherita")

        # Assert
        assert [product.id for product in results] == ["A", "B"]

    def test_unavailable_matches_are_sorted_after_available_ones(self):
        # Arrange
        available = Product(id="A", name="Cola Rece", category=Category.DRINK, price_bani=100)
        unavailable = Product(
            id="B", name="Cola Calda", category=Category.DRINK, price_bani=100, available=False
        )
        catalog = Catalog(products=(unavailable, available))

        # Act
        results = catalog.search("cola")

        # Assert
        assert [product.id for product in results] == ["A", "B"]


class TestById:
    def test_returns_none_for_unknown_product(self, catalog: Catalog):
        # Arrange
        product_id = "DOES-NOT-EXIST"

        # Act
        result = catalog.by_id(product_id)

        # Assert
        assert result is None

    def test_returns_matching_product(self, catalog: Catalog):
        # Arrange
        product_id = "PZ-001"

        # Act
        result = catalog.by_id(product_id)

        # Assert
        assert result is not None
        assert result.name == "Margherita"


class TestByCategory:
    def test_returns_only_products_of_requested_category(self, catalog: Catalog):
        # Arrange
        category = Category.SAUCE

        # Act
        results = catalog.by_category(category)

        # Assert
        assert len(results) == 4
        assert all(product.category == Category.SAUCE for product in results)

    def test_returns_empty_tuple_when_no_products_match(self):
        # Arrange
        pizza = Product(
            id="X",
            name="X",
            category=Category.PIZZA,
            sizes=(
                SizeOption(
                    code="small", label="mica", diameter_cm=25,
                    price_bani=100, oven_slots=1, bake_minutes=5,
                ),
            ),
        )
        catalog = Catalog(products=(pizza,))

        # Act
        results = catalog.by_category(Category.DRINK)

        # Assert
        assert results == ()
