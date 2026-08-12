"""Client HTTP peste API-ul din Faza 1.

Agentul nu importa niciodata domeniul direct: vorbeste cu backend-ul exact prin
rutele pe care le va folosi si agentul vocal din Faza 3. Asa, ce trece in evals
pe text trece si pe voce — nu exista o a doua cale, mai permisiva, prin care sa
se strecoare o comanda gresita.

Traducerea erorilor:

- 422 e un refuz motivat de domeniu (`{"code", "message", "field"}`). Nu e un bug:
  e raspunsul corect la „nu livram acolo" sau „sub comanda minima". Ajunge la LLM
  ca rezultat de tool marcat eroare, ca el sa il *povesteasca* — nu sa il inventeze.
- Orice altceva (retea, 404 pe sesiune expirata, 5xx) e o defectiune. Ajunge tot
  la LLM, dar cu un cod distinct, ca sa poata escalada in loc sa reincerce la infinit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

#: Coduri pe care le producem noi, nu domeniul. Prefixate, ca sa nu se confunde
#: niciodata cu un `code` venit din `ValidationIssue`.
TRANSPORT_ERROR = "agent_transport_error"
UNEXPECTED_STATUS = "agent_unexpected_status"
SESSION_LOST = "agent_session_lost"


@dataclass(frozen=True, slots=True)
class ApiResult:
    """Rezultatul unui apel: reusit (`data`) sau refuzat/esuat (`code` + `message`)."""

    ok: bool
    data: Any = None
    code: str = ""
    message: str = ""
    field: str | None = None

    @property
    def is_domain_refusal(self) -> bool:
        """Refuz motivat de domeniu (422), nu defectiune. Conversatia continua."""
        return not self.ok and self.code not in (
            TRANSPORT_ERROR,
            UNEXPECTED_STATUS,
            SESSION_LOST,
        )


class PizzaApiClient:
    """Apeluri sincrone catre API. Un client pe sesiune de apel."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        #: `http_client` injectabil ca testele si evals-urile sa poata da
        #: `TestClient(app)` — acelasi HTTP, aceleasi rute si aceleasi coduri de
        #: eroare, dar in proces, fara server pornit. Nu e o a doua cale catre
        #: backend, e acelasi drum pe alt transport.
        self._http = http_client or httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> PizzaApiClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ----------------------------------------------------------------- sesiune

    def create_session(self) -> str:
        """Deschide o sesiune de apel. Ridica exceptia — fara sesiune nu e nimic de facut."""
        response = self._http.post("/api/sessions")
        response.raise_for_status()
        return str(response.json()["session_id"])

    def get_session(self, sid: str) -> ApiResult:
        return self._request("GET", f"/api/sessions/{sid}")

    # -------------------------------------------------------------------- cos

    def search_menu(self, query: str | None = None, category: str | None = None) -> ApiResult:
        params = {k: v for k, v in (("query", query), ("category", category)) if v}
        return self._request("GET", "/api/menu", params=params)

    def add_item(self, sid: str, body: dict[str, Any]) -> ApiResult:
        return self._request("POST", f"/api/sessions/{sid}/items", json=body)

    def update_item(self, sid: str, line_id: str, body: dict[str, Any]) -> ApiResult:
        return self._request("PATCH", f"/api/sessions/{sid}/items/{line_id}", json=body)

    def remove_item(self, sid: str, line_id: str) -> ApiResult:
        return self._request("DELETE", f"/api/sessions/{sid}/items/{line_id}")

    # ------------------------------------------------------- livrare / contact

    def set_fulfillment(self, sid: str, fulfillment: str) -> ApiResult:
        return self._request(
            "PUT", f"/api/sessions/{sid}/fulfillment", json={"fulfillment": fulfillment}
        )

    def resolve_address(self, sid: str, text: str) -> ApiResult:
        return self._request("POST", f"/api/sessions/{sid}/address", json={"text": text})

    def set_contact(self, sid: str, phone: str, name: str = "") -> ApiResult:
        return self._request(
            "PUT", f"/api/sessions/{sid}/contact", json={"phone": phone, "name": name}
        )

    def set_payment(self, sid: str, payment: str) -> ApiResult:
        return self._request("PUT", f"/api/sessions/{sid}/payment", json={"payment": payment})

    def mark_asked(self, sid: str, category: str) -> ApiResult:
        return self._request("PUT", f"/api/sessions/{sid}/asked/{category}")

    # ------------------------------------------------------------- final apel

    def get_order_summary(self, sid: str) -> ApiResult:
        return self._request("GET", f"/api/sessions/{sid}/summary")

    def place_order(self, sid: str, idempotency_key: str) -> ApiResult:
        return self._request(
            "POST", f"/api/sessions/{sid}/place", json={"idempotency_key": idempotency_key}
        )

    # ------------------------------------------------------------------ intern

    def _request(self, method: str, url: str, **kwargs: Any) -> ApiResult:
        try:
            response = self._http.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            return ApiResult(
                ok=False,
                code=TRANSPORT_ERROR,
                message=f"Backend-ul nu raspunde ({type(exc).__name__}).",
            )
        return _to_result(response)


def _to_result(response: httpx.Response) -> ApiResult:
    if response.is_success:
        return ApiResult(ok=True, data=response.json())
    if response.status_code == 422:
        issue = _safe_json(response)
        return ApiResult(
            ok=False,
            code=str(issue.get("code", "domain_error")),
            message=str(issue.get("message", "Cererea a fost refuzata.")),
            field=issue.get("field"),
        )
    if response.status_code == 404:
        return ApiResult(
            ok=False,
            code=SESSION_LOST,
            message="Sesiunea de apel nu mai exista pe server.",
        )
    return ApiResult(
        ok=False,
        code=UNEXPECTED_STATUS,
        message=f"Backend-ul a raspuns cu status {response.status_code}.",
    )


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}
