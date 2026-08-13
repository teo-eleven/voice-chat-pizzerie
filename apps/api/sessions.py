"""Sesiuni de apel, in memorie.

Faza 1 nu are nevoie ca un cos sa supravietuiasca unei deconectari — mutarea
in DB e Faza 5. Fiecare pornire a aplicatiei (sau, la teste, fiecare
`TestClient`) incepe cu un depozit gol si un contor de ID-uri de la zero,
pentru reproductibilitate.

`CallSession` e imutabila, ca modelele de domeniu: orice schimbare de stare
produce un obiect nou, salvat inapoi in depozit prin `SessionStore.update`.

Rutele sincrone FastAPI ruleaza in thread-pool, asa ca doua tool-call-uri
(sau un retry de retea) pe aceeasi sesiune ar putea face
`citeste -> calculeaza -> salveaza` in paralel si pierde una dintre
modificari. `SessionStore` tine un `threading.Lock` si expune `update`, care
face tot ciclul (citire, aplicare, scriere) atomic — sub lock, de la un cap
la altul. Contorul de ID-uri e protejat de acelasi lock.

Depozitul are si doua plafoane de siguranta, ca un apelant sa nu umple
memoria procesului: TTL de inactivitate si numar maxim de sesiuni active.
Ceasul e injectabil (`now`), ca testele sa fie deterministe fara `sleep`.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from packages.domain.enums import Fulfillment, PaymentMethod
from packages.domain.errors import DomainError
from packages.domain.models import Address, Cart, Contact

#: Categoriile din checklist-ul de upsell al agentului (Faza 2).
_ASKED_CATEGORIES = ("sauce", "drink", "dessert")

#: Implicite pentru plafoanele sesiunilor in memorie; suprascrise din mediu.
_DEFAULT_SESSION_TTL_MINUTES = 30
_DEFAULT_SESSION_MAX_ACTIVE = 500

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class CallSession(BaseModel):
    """Starea unui apel in curs: cosul, tipul de preluare, adresa, contactul, plata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str
    cart: Cart = Cart()
    fulfillment: Fulfillment = Fulfillment.DELIVERY
    address: Address | None = None
    contact: Contact | None = None
    payment: PaymentMethod | None = None
    allergy_note: str | None = None
    asked_flags: dict[str, bool] = Field(
        default_factory=lambda: dict.fromkeys(_ASKED_CATEGORIES, False)
    )
    placed_order_id: str | None = None


class SessionStore:
    """Depozit in memorie: `session_id` -> `CallSession`, cu ID-uri deterministe.

    `update(session_id, fn)` e singura cale de mutatie: tine lock-ul pe toata
    durata citire -> aplica `fn` -> scrie, ca doua request-uri paralele pe
    aceeasi sesiune sa nu se calce pe coada.
    """

    def __init__(
        self,
        *,
        ttl_minutes: int | None = None,
        max_sessions: int | None = None,
        now: Clock | None = None,
    ) -> None:
        self._sessions: dict[str, CallSession] = {}
        self._last_seen: dict[str, datetime] = {}
        self._counter = 0
        self._lock = threading.Lock()
        self._ttl = timedelta(
            minutes=(
                ttl_minutes
                if ttl_minutes is not None
                else _env_int("SESSION_TTL_MINUTES", _DEFAULT_SESSION_TTL_MINUTES)
            )
        )
        self._max_sessions = (
            max_sessions
            if max_sessions is not None
            else _env_int("SESSION_MAX_ACTIVE", _DEFAULT_SESSION_MAX_ACTIVE)
        )
        #: Ceas injectabil, ca testele de expirare sa fie deterministe fara `sleep`.
        self.now: Clock = now or _utc_now

    def create(self) -> CallSession:
        with self._lock:
            self._evict_expired_locked()
            if len(self._sessions) >= self._max_sessions:
                raise DomainError.of(
                    "too_many_sessions",
                    "Sunt prea multe sesiuni active in acest moment. "
                    "Reincercati in cateva minute.",
                )
            self._counter += 1
            session = CallSession(session_id=f"S{self._counter}")
            self._sessions[session.session_id] = session
            self._last_seen[session.session_id] = self.now()
            return session

    def get(self, session_id: str) -> CallSession | None:
        with self._lock:
            return self._touch_locked(session_id)

    def update(
        self, session_id: str, fn: Callable[[CallSession], CallSession]
    ) -> CallSession | None:
        """Citeste, aplica `fn` si salveaza — atomic, sub lock.

        `None` daca sesiunea nu exista (sau a expirat), ca ruta sa raspunda 404
        fara sa fi executat `fn` pe o stare inventata.
        """
        with self._lock:
            session = self._touch_locked(session_id)
            if session is None:
                return None
            updated = fn(session)
            self._sessions[session_id] = updated
            self._last_seen[session_id] = self.now()
            return updated

    def _touch_locked(self, session_id: str) -> CallSession | None:
        """Necesita lock-ul tinut. Expira sesiunea daca a trecut TTL-ul de
        inactivitate, altfel ii actualizeaza marcajul de activitate."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        last_seen = self._last_seen.get(session_id, self.now())
        if self.now() - last_seen > self._ttl:
            del self._sessions[session_id]
            self._last_seen.pop(session_id, None)
            return None
        self._last_seen[session_id] = self.now()
        return session

    def _evict_expired_locked(self) -> None:
        """Necesita lock-ul tinut. Elibereaza memoria sesiunilor inactive de mult."""
        now = self.now()
        expired = [
            sid for sid, last_seen in self._last_seen.items() if now - last_seen > self._ttl
        ]
        for sid in expired:
            self._sessions.pop(sid, None)
            self._last_seen.pop(sid, None)
