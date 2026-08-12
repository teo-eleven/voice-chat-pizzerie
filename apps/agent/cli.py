"""Chat text in terminal, cu exact aceleasi tool-uri ca agentul vocal.

Rostul lui e viteza de iteratie: pe text ajustezi promptul in secunde, nu in
apeluri de trei minute. Ce trece aici trece si pe voce, fiindca amandoua trec
prin acelasi backend si acelasi contract de tool-uri.

    uv run python -m apps.agent.cli

Comenzi: /tools (arata apelurile), /stare (checklist-ul), /iesire.
"""

from __future__ import annotations

import sys

import anthropic
import httpx

from packages.env import load_env

from .agent import OrderAgent
from .api_client import PizzaApiClient
from .config import AgentConfig

_BANNER = """Pizzeria Punto — agent text (Faza 2)
Model: {model} · efort: {effort} · API: {api}
Sesiune: {sid}

Scrie ca si cum ai vorbi la telefon. /tools · /stare · /iesire
"""


def main() -> int:
    load_env()
    config = AgentConfig.from_env()

    if (problem := _preflight(config)) is not None:
        print(problem, file=sys.stderr)
        return 1

    agent = OrderAgent.start(config)
    print(
        _BANNER.format(
            model=config.model,
            effort=config.effort,
            api=config.api_base_url,
            sid=agent.session_id,
        )
    )

    show_tools = False
    try:
        while True:
            try:
                user_text = input("client> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0

            if not user_text:
                continue
            if user_text in ("/iesire", "/quit", "/exit"):
                return 0
            if user_text == "/tools":
                show_tools = not show_tools
                print(f"[apelurile de tool: {'vizibile' if show_tools else 'ascunse'}]\n")
                continue
            if user_text == "/stare":
                print(_state_block(agent), end="\n\n")
                continue

            turn = agent.say(user_text)

            if show_tools:
                for call in turn.tool_calls:
                    marker = "!!" if call["is_error"] else "->"
                    print(f"  {marker} {call['tool']}({_short(call['input'])})")
                    print(f"     {_short(call['output'], 160)}")

            print(f"agent > {turn.text}\n")

            if turn.placed_order_id:
                print(f"[comanda plasata: {turn.placed_order_id}]\n")
            if turn.escalated:
                escalation = agent.escalation or {}
                print(f"[escaladare: {escalation.get('reason')} — {escalation.get('note')}]\n")
                return 0
    finally:
        agent.close()


def _preflight(config: AgentConfig) -> str | None:
    """Verifica cele doua lucruri fara de care nu are rost sa pornim."""
    try:
        anthropic.Anthropic()
    except Exception as exc:  # noqa: BLE001 — orice problema de credentiale, la fel
        return f"Nu pot initializa clientul Anthropic: {exc}\nSeteaza ANTHROPIC_API_KEY in .env."

    try:
        with PizzaApiClient(config.api_base_url, timeout=config.http_timeout) as client:
            client.create_session()
    except httpx.HTTPError:
        return (
            f"API-ul nu raspunde la {config.api_base_url}.\n"
            "Porneste-l intr-un alt terminal:\n"
            "  uv run uvicorn apps.api.main:app --reload"
        )
    return None


def _state_block(agent: OrderAgent) -> str:
    from . import checklist as checklist_module
    from .agent import _parse_session

    result = agent.api.get_session(agent.session_id)
    if not result.ok:
        return f"[stare indisponibila: {result.message}]"
    session = _parse_session(result.data)
    return checklist_module.render(checklist_module.from_session(session))


def _short(value: object, limit: int = 90) -> str:
    text = str(value)
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


if __name__ == "__main__":
    raise SystemExit(main())
