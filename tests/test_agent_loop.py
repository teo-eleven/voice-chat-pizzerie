"""Bucla de conversatie, cu un LLM fals in loc de model real.

Ce se verifica aici e mecanica harness-ului, nu inteligenta: ca un `tool_use`
chiar ajunge la backend, ca rezultatele se intorc intr-un singur mesaj, ca
istoricul pastreaza raspunsul modelului neatins, si ca cele doua iesiri de
avarie (refuz si bucla de tool-uri) escaladeaza in loc sa se blocheze.

Calitatea conversatiei — ce intreaba, in ce ordine, cum formuleaza — nu se poate
testa asa. Aceea se masoara pe scenariile din `evals/`, cu modelul real.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.agent.agent import OrderAgent
from apps.agent.api_client import PizzaApiClient
from apps.agent.config import AgentConfig
from apps.api.main import app


class FakeLLM:
    """Reda raspunsuri pregatite si retine cererile primite, ca sa le putem inspecta."""

    def __init__(self, *responses: SimpleNamespace) -> None:
        self._responses = list(responses)
        self.requests: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs) -> SimpleNamespace:
        self.requests.append(kwargs)
        # Ultimul raspuns se repeta: asa se poate simula un model care cere
        # tool-uri la nesfarsit, fara sa pregatim zeci de raspunsuri identice.
        return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]


def _text(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use(tool_id: str, name: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=payload)


def _reply(stop_reason: str, *content: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop_reason, content=list(content))


@pytest.fixture
def make_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    clients: list[TestClient] = []

    def factory(llm: FakeLLM) -> OrderAgent:
        test_client = TestClient(app)
        test_client.__enter__()
        clients.append(test_client)
        api = PizzaApiClient("http://testserver", http_client=test_client)
        return OrderAgent(
            api=api,
            session_id=api.create_session(),
            config=AgentConfig(max_tool_rounds=3),
            llm=llm,
            system_prompt="prompt de test",
        )

    yield factory
    for test_client in clients:
        test_client.__exit__(None, None, None)


class TestToolLoop:
    def test_tool_call_reaches_the_backend_and_the_answer_comes_back(self, make_agent):
        llm = FakeLLM(
            _reply(
                "tool_use",
                _tool_use(
                    "t1", "add_item", {"product_id": "PZ-002", "qty": 1, "size_code": "large"}
                ),
            ),
            _reply("end_turn", _text("Am notat o Capricciosa.")),
        )
        agent = make_agent(llm)

        turn = agent.say("o capricciosa mare")

        assert turn.text == "Am notat o Capricciosa."
        assert [call["tool"] for call in turn.tool_calls] == ["add_item"]
        assert not turn.tool_calls[0]["is_error"]
        # Cosul chiar s-a schimbat pe server, nu doar in raspunsul modelului.
        assert agent.api.get_session(agent.session_id).data["cart"]["lines"]

    def test_parallel_tool_calls_return_in_a_single_message(self, make_agent):
        """Rezultatele imprastiate pe mai multe mesaje invata modelul sa nu mai
        ceara tool-uri in paralel — de aceea merg toate intr-unul singur."""
        llm = FakeLLM(
            _reply(
                "tool_use",
                _tool_use("t1", "search_menu", {"query": "capricciosa"}),
                _tool_use("t2", "search_menu", {"category": "drink"}),
            ),
            _reply("end_turn", _text("Gata.")),
        )
        agent = make_agent(llm)

        turn = agent.say("ce aveti?")

        tool_result_messages = [
            message
            for message in llm.requests[-1]["messages"]
            if isinstance(message.get("content"), list)
            and all(
                isinstance(block, dict) and block.get("type") == "tool_result"
                for block in message["content"]
            )
        ]
        assert len(turn.tool_calls) == 2
        assert len(tool_result_messages) == 1
        assert len(tool_result_messages[0]["content"]) == 2

    def test_state_snapshot_is_injected_before_the_model_answers(self, make_agent):
        llm = FakeLLM(_reply("end_turn", _text("Bună ziua!")))
        agent = make_agent(llm)

        agent.say("alo")

        messages = llm.requests[0]["messages"]
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "system"
        assert "Stare curenta pe server" in messages[1]["content"]

    def test_system_prompt_is_cached_and_tools_are_sent(self, make_agent):
        llm = FakeLLM(_reply("end_turn", _text("Bună ziua!")))
        agent = make_agent(llm)

        agent.say("alo")

        request = llm.requests[0]
        assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert {tool["name"] for tool in request["tools"]} >= {"add_item", "place_order"}

    def test_domain_refusal_is_marked_as_an_error_result(self, make_agent):
        llm = FakeLLM(
            _reply("tool_use", _tool_use("t1", "add_item", {"product_id": "NU-EXISTA"})),
            _reply("end_turn", _text("Nu avem produsul acesta.")),
        )
        agent = make_agent(llm)

        turn = agent.say("vreau un cheeseburger")

        assert turn.tool_calls[0]["is_error"]
        assert "product_not_found" in turn.tool_calls[0]["output"]


class TestFailSafes:
    def test_model_refusal_escalates_instead_of_going_silent(self, make_agent):
        agent = make_agent(FakeLLM(_reply("refusal")))

        turn = agent.say("ceva ce modelul refuza")

        assert turn.escalated
        assert turn.stop_reason == "refusal"
        assert agent.escalation["reason"] == "provider_failure"

    def test_endless_tool_calls_hit_the_round_cap_and_escalate(self, make_agent):
        """Un model care cere tool-uri la nesfarsit trebuie sa predea apelul, nu
        sa tina clientul pe linie."""
        llm = FakeLLM(_reply("tool_use", _tool_use("t1", "search_menu", {"query": "x"})))
        agent = make_agent(llm)

        turn = agent.say("alo")

        assert turn.stop_reason == "tool_loop_exhausted"
        assert turn.escalated
        assert len(turn.tool_calls) == 3  # max_tool_rounds din config-ul de test
