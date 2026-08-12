"""Configurarea agentului: model, efort, adresa API-ului, plafoane.

Totul se poate suprascrie din mediu, ca evals-urile si Faza 3 (voce) sa poata
varia setarile fara sa atinga codul.

Nota despre `AGENT_EFFORT`: pentru un agent vocal latenta e o metrica de produs
(Faza 3 tinteste p50 sub 1.2s de la sfarsitul vorbirii la primul audio), iar
munca grea o face backend-ul — LLM-ul doar interpreteaza si povesteste. De aceea
implicitul e `low`. Ridica-l daca evals-urile arata interpretari gresite.

Nota despre thinking: ramane pornit (adaptive). Cu thinking dezactivat, modelul
poate scrie ocazional apelul de tool ca text simplu in loc de bloc `tool_use` —
tura se incheie cu succes, dar apelul nu ruleaza niciodata. Pentru un agent care
construieste o comanda reala, o comanda pierduta in tacere e inacceptabila.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: Modelul implicit. Vezi nota de efort de mai sus inainte sa cobori tier-ul.
_DEFAULT_MODEL = "claude-opus-5"
_DEFAULT_EFFORT = "low"
_DEFAULT_MAX_TOKENS = 4096
_DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
_DEFAULT_HTTP_TIMEOUT = 10.0

#: Plafon de tururi model<->tool intr-o singura replica a clientului. Un apel
#: normal face 1-3; peste plafon inseamna bucla, nu conversatie.
_DEFAULT_MAX_TOOL_ROUNDS = 12


@dataclass(frozen=True, slots=True)
class AgentConfig:
    model: str = _DEFAULT_MODEL
    effort: str = _DEFAULT_EFFORT
    max_tokens: int = _DEFAULT_MAX_TOKENS
    api_base_url: str = _DEFAULT_API_BASE_URL
    http_timeout: float = _DEFAULT_HTTP_TIMEOUT
    max_tool_rounds: int = _DEFAULT_MAX_TOOL_ROUNDS

    @classmethod
    def from_env(cls) -> AgentConfig:
        return cls(
            model=os.environ.get("AGENT_MODEL", _DEFAULT_MODEL),
            effort=os.environ.get("AGENT_EFFORT", _DEFAULT_EFFORT),
            max_tokens=_env_int("AGENT_MAX_TOKENS", _DEFAULT_MAX_TOKENS),
            api_base_url=os.environ.get("API_BASE_URL", _DEFAULT_API_BASE_URL).rstrip("/"),
            http_timeout=_env_float("AGENT_HTTP_TIMEOUT", _DEFAULT_HTTP_TIMEOUT),
            max_tool_rounds=_env_int("AGENT_MAX_TOOL_ROUNDS", _DEFAULT_MAX_TOOL_ROUNDS),
        )


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default
