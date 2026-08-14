"""Scriere atomică de fișiere JSON din `data/`.

Fișierele din `data/` sunt citite de API la pornire și rescrise de scripturile de
administrare. O scriere întreruptă la mijloc ar lăsa un JSON trunchiat, iar
următoarea pornire a serverului ar eșua — de aici scrierea în temporar plus
`replace`, care e atomic pe același sistem de fișiere.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

#: Permisiunile fișierelor din `data/`: citibile de oricine, scrise de proprietar.
DATA_FILE_MODE = 0o644


def write_json_atomic(path: Path, payload: Any, file_mode: int = DATA_FILE_MODE) -> None:  # noqa: ANN401
    """Serializează `payload` ca JSON și îl pune la `path` fără stare intermediară.

    `mkstemp` creează fișierul atomic și întoarce un descriptor deja deschis; o
    întrerupere la mijloc lasă temporarul pe disc, nu fișierul destinație corupt.

    Permisiunile se pun explicit: `mkstemp` creează cu `0600`, iar `replace`
    păstrează modul temporarului. Fără linia asta, fiecare rulare a unui script ar
    strânge tăcut drepturile fișierului față de restul lui `data/`, iar serverul
    rulat sub alt utilizator ar ajunge să nu-și mai poată citi propriile date.

    `payload` e `Any` fiindcă asta acceptă și `json.dumps` — validarea formei se
    face de apelant, cu modelul lui Pydantic, înainte să ajungă aici.
    """
    content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(content)
        tmp_path.chmod(file_mode)
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
