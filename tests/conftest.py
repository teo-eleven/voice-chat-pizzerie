"""Fixture partajate de suita de teste.

`client` stă aici, nu într-un fișier de test, ca să fie vizibil pentru toate modulele
fără import — un fixture importat dintr-un alt test devine un nume redefinit în
semnătura funcției, iar linterul îl semnalează pe bună dreptate.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # Fiecare test primeste un fisier SQLite propriu, izolat de restul suitei.
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    with TestClient(app) as test_client:
        yield test_client
