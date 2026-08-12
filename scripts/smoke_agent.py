"""Smoke Faza 2: o comandă completă purtată de model, cu apelurile de tool la vedere.

Rulează fără server pornit — API-ul e montat în proces, prin `TestClient`, exact
pe rutele pe care le va folosi și agentul vocal din Faza 3.

    uv run python scripts/smoke_agent.py

Cere `ANTHROPIC_API_KEY` în `.env`. Consumă tokeni: e un apel real, cu model real.
Replicile clientului sunt fixe, deci scriptul e o verificare de instalație, nu o
măsurătoare — comportamentul conversațional se măsoară pe `evals/scenarios/`.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

# rulează de oriunde: rădăcina proiectului e părintele lui scripts/
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # catalogul și config-urile se încarcă din căi relative la rădăcină
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mktemp(suffix='.db')}")

from fastapi.testclient import TestClient  # noqa: E402

from apps.agent.agent import OrderAgent  # noqa: E402
from apps.agent.api_client import PizzaApiClient  # noqa: E402
from apps.agent.config import AgentConfig  # noqa: E402
from apps.api.main import app  # noqa: E402
from packages.env import load_env  # noqa: E402

load_env()

#: Comandă simplă, dusă până la plasare. Adresa e din fixture-ul de geocodare.
SCRIPT = (
    "bună ziua, aș vrea două capricciosa mari",
    "livrare, pe Aleea Nucșoara 4",
    "nu, mulțumesc, doar atât",
    "0722334455",
    "Teodor",
    "cash",
    "da, e bine, comand",
)


def main() -> int:
    with TestClient(app) as test_client:
        api = PizzaApiClient("http://testserver", http_client=test_client)
        agent = OrderAgent(api=api, session_id=api.create_session(), config=AgentConfig.from_env())
        print(f"model={agent.config.model} · efort={agent.config.effort}")
        print("=" * 70)

        total_seconds = 0.0
        for line in SCRIPT:
            started = time.monotonic()
            turn = agent.say(line)
            elapsed = time.monotonic() - started
            total_seconds += elapsed

            print(f"\nCLIENT: {line}")
            for call in turn.tool_calls:
                marker = "!!" if call["is_error"] else "->"
                print(f"   {marker} {call['tool']}({call['input']})")
                print(f"      {_short(call['output'])}")
            print(f"AGENT : {turn.text}")
            print(f"   [{elapsed:.1f}s · stop={turn.stop_reason}]")

            if turn.escalated:
                print(f"\n### ESCALADAT: {agent.escalation}")
                break
            if turn.placed_order_id:
                print(f"\n### COMANDĂ PLASATĂ: {turn.placed_order_id}")
                break

        print("\n" + "=" * 70)
        print(f"{total_seconds:.1f}s · {len(agent.tool_calls)} apeluri de tool")

        state = api.get_session(agent.session_id)
        if state.ok:
            cart = state.data["cart"]
            lei = cart["total_bani"] / 100
            print(f"coș final: {len(cart['lines'])} linii · total {lei:.2f} lei")
    return 0


def _short(text: str, limit: int = 150) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"


if __name__ == "__main__":
    raise SystemExit(main())
