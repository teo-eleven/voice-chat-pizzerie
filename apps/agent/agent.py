"""Bucla de conversatie: model + tool-uri + starea reala a comenzii.

Bucla e scrisa de mana, nu prin tool runner-ul din SDK, din doua motive care
conteaza aici: evals-urile din Faza 2 au nevoie de urma completa a fiecarui apel
de tool (ce a cerut, ce a raspuns backend-ul), iar Faza 5 cere exact acelasi log
per apel ca sa poti spune unde s-a rupt o comanda gresita — la STT, la LLM sau la
validare.

Doua detalii de context care nu se vad din cod:

- Promptul de sistem si definitiile de tool-uri sunt stabile pe toata durata
  apelului, deci se pun in cache. Se plateste o singura data pe apel, nu la
  fiecare replica.
- Starea comenzii se injecteaza ca mesaj cu rolul `system` *in interiorul*
  conversatiei, nu prin rescrierea promptului de sistem. Rescrierea ar schimba
  inceputul prefixului si ar invalida cache-ul la fiecare tur; asa, istoricul
  ramane in cache si starea proaspata vine dupa el, pe canalul de operator.
  Modelele care nu accepta rolul `system` la mijloc primesc acelasi text in
  turul utilizatorului (vezi `_MODEL_REJECTS_MID_SYSTEM`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic

from . import checklist as checklist_module
from . import tools as tools_module
from .api_client import PizzaApiClient
from .config import AgentConfig
from .tools import ToolContext

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_SYSTEM_PROMPT_VERSION = "system_v1"

#: Mesajul de eroare prin care API-ul semnaleaza ca modelul nu accepta rolul
#: `system` la mijlocul conversatiei. Verificat o singura data, apoi memorat.
_MODEL_REJECTS_MID_SYSTEM = "role 'system' is not supported"

_REFUSAL_REPLY = "Îmi pare rău, nu pot continua cu asta. Vă dau legătura cu un coleg."
_LOOP_GUARD_REPLY = "Am o problemă tehnică și nu reușesc să finalizez. Vă dau legătura cu un coleg."


@dataclass(frozen=True, slots=True)
class AgentTurn:
    """Ce a produs o replica a clientului."""

    text: str
    tool_calls: tuple[dict[str, Any], ...]
    escalated: bool
    placed_order_id: str | None
    stop_reason: str


@dataclass
class OrderAgent:
    """Un apel. Tine istoricul, sesiunea de pe server si urma de tool-uri."""

    api: PizzaApiClient
    session_id: str
    config: AgentConfig = field(default_factory=AgentConfig.from_env)
    llm: anthropic.Anthropic = field(default_factory=anthropic.Anthropic)
    system_prompt: str = field(default_factory=lambda: load_system_prompt())

    _messages: list[dict[str, Any]] = field(default_factory=list, init=False)
    _context: ToolContext = field(init=False)
    _mid_conversation_system: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        self._context = ToolContext(client=self.api, session_id=self.session_id)

    # ------------------------------------------------------------------ public

    @classmethod
    def start(cls, config: AgentConfig | None = None, **kwargs: Any) -> OrderAgent:
        """Deschide o sesiune noua pe server si construieste agentul peste ea."""
        resolved = config or AgentConfig.from_env()
        api = PizzaApiClient(resolved.api_base_url, timeout=resolved.http_timeout)
        return cls(api=api, session_id=api.create_session(), config=resolved, **kwargs)

    @property
    def escalation(self) -> dict[str, str] | None:
        return self._context.escalation

    @property
    def placed_order_id(self) -> str | None:
        return self._context.placed_order_id

    @property
    def tool_calls(self) -> tuple[dict[str, Any], ...]:
        """Urma completa a apelurilor, in ordine. Baza evals-urilor si a log-ului."""
        return tuple(self._context.calls)

    def say(self, user_text: str) -> AgentTurn:
        """Trimite o replica a clientului si returneaza raspunsul agentului."""
        calls_before = len(self._context.calls)
        self._messages.append({"role": "user", "content": user_text})
        self._append_state_snapshot()

        text, stop_reason = self._run_tool_loop()
        return AgentTurn(
            text=text,
            tool_calls=tuple(self._context.calls[calls_before:]),
            escalated=self._context.escalation is not None,
            placed_order_id=self._context.placed_order_id,
            stop_reason=stop_reason,
        )

    def close(self) -> None:
        self.api.close()

    # ------------------------------------------------------------------ intern

    def _run_tool_loop(self) -> tuple[str, str]:
        for _round in range(self.config.max_tool_rounds):
            response = self._create_message()

            if response.stop_reason == "refusal":
                self._context.escalation = {"reason": "provider_failure", "note": "refuz model"}
                return _REFUSAL_REPLY, "refusal"

            self._messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason != "tool_use":
                return _text_of(response.content), str(response.stop_reason)

            self._messages.append(
                {"role": "user", "content": self._execute_tools(response.content)}
            )

        # Plafonul de tururi s-a atins: modelul se invarte in loc sa raspunda.
        self._context.escalation = {"reason": "provider_failure", "note": "bucla de tool-uri"}
        return _LOOP_GUARD_REPLY, "tool_loop_exhausted"

    def _create_message(self) -> Any:
        try:
            return self._send(self._messages)
        except anthropic.BadRequestError as exc:
            if not (self._mid_conversation_system and _MODEL_REJECTS_MID_SYSTEM in str(exc)):
                raise
            # Modelul nu accepta rolul `system` la mijloc: mutam blocurile de
            # stare in turul utilizatorului si nu mai incercam varianta cu system.
            self._mid_conversation_system = False
            self._messages = [_as_user_note(message) for message in self._messages]
            return self._send(self._messages)

    def _send(self, messages: list[dict[str, Any]]) -> Any:
        return self.llm.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": self.config.effort},
            system=[
                {
                    "type": "text",
                    "text": self.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=tools_module.TOOLS,
            messages=messages,
        )

    def _execute_tools(self, content: list[Any]) -> list[dict[str, Any]]:
        """Executa toate `tool_use` din replica si intoarce rezultatele intr-un singur mesaj.

        Toate intr-unul singur, deliberat: rezultatele imprastiate pe mai multe
        mesaje invata modelul sa nu mai ceara tool-uri in paralel.
        """
        results: list[dict[str, Any]] = []
        for block in content:
            if getattr(block, "type", None) != "tool_use":
                continue
            outcome = tools_module.dispatch(self._context, block.name, dict(block.input))
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": outcome.content,
                    "is_error": outcome.is_error,
                }
            )
        return results

    def _append_state_snapshot(self) -> None:
        """Injecteaza starea reala a comenzii inaintea raspunsului modelului."""
        snapshot = self._state_snapshot()
        if snapshot is None:
            return
        if self._mid_conversation_system:
            self._messages.append({"role": "system", "content": snapshot})
        else:
            self._messages.append({"role": "user", "content": f"[stare]\n{snapshot}"})

    def _state_snapshot(self) -> str | None:
        result = self.api.get_session(self.session_id)
        if not result.ok:
            return None
        session = _parse_session(result.data)
        return checklist_module.render(checklist_module.from_session(session))


def load_system_prompt(version: str = _SYSTEM_PROMPT_VERSION) -> str:
    return (_PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8")


def _parse_session(payload: Any) -> Any:
    from apps.api.sessions import CallSession

    return CallSession.model_validate(payload)


def _as_user_note(message: dict[str, Any]) -> dict[str, Any]:
    if message.get("role") != "system":
        return message
    return {"role": "user", "content": f"[stare]\n{message['content']}"}


def _text_of(content: list[Any]) -> str:
    parts = [block.text for block in content if getattr(block, "type", None) == "text"]
    return "\n".join(part for part in parts if part).strip()
