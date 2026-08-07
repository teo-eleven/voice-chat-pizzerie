/**
 * api.js — helper partajat de cele trei ecrane (index / kitchen / driver).
 *
 * Scop, strict:
 *   1. `apiRequest`/`apiGet`/`apiPost`/`apiPut`/`apiPatch`/`apiDelete` — fetch spre `/api/...`,
 *      cu parsare uniformă a erorilor (422 de domeniu, 404 simplu, 422 de validare Pydantic).
 *   2. `formatBani` — formatare de afișare a unui întreg în bani, deja calculat de server.
 *      Nu adună, nu scade, nu calculează niciun total — doar formatează un număr dat.
 *   3. `createErrorLog` — afișare vizibilă a erorilor (cod, mesaj, câmp), nu `alert()`.
 *
 * În plus, `connectOrdersSocket` (reconectare cu backoff la `/ws/orders`) e păstrat aici
 * și nu duplicat în kitchen.html/driver.html, fiindcă cele două ecrane au nevoie de exact
 * aceeași logică — a o repeta ar fi dus la două copii care se pot dezalinia (regulă DRY).
 */

(function (global) {
  "use strict";

  const BANI_PER_LEU = 100;

  // --------------------------------------------------------------------- erori

  class ApiError extends Error {
    constructor(issue) {
      super(issue.message);
      this.code = issue.code;
      this.field = issue.field;
      this.status = issue.status;
    }
  }

  /** Normalizează cele trei forme posibile de corp de eroare HTTP ale API-ului. */
  function normalizeErrorBody(status, data) {
    if (data && typeof data === "object" && "code" in data && "message" in data) {
      // Forma domeniului: {code, message, field} — vine din DomainError.
      return { code: data.code, message: data.message, field: data.field ?? null, status };
    }
    if (data && typeof data.detail === "string") {
      // HTTPException simplu (ex: sesiune inexistentă) — {detail: "..."}.
      return { code: null, message: data.detail, field: null, status };
    }
    if (data && Array.isArray(data.detail)) {
      // 422 de validare Pydantic, înainte să ajungă la ruta de domeniu.
      const first = data.detail[0] ?? {};
      const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : null;
      const message = data.detail
        .map((item) => {
          const loc = Array.isArray(item.loc) ? item.loc.join(".") : "";
          return loc ? `${loc}: ${item.msg}` : item.msg;
        })
        .join(" | ");
      return { code: "validation_error", message: message || "Cerere invalidă.", field, status };
    }
    return { code: null, message: `Eroare HTTP ${status}.`, field: null, status };
  }

  async function apiRequest(method, path, body) {
    let response;
    try {
      response = await fetch(path, {
        method,
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (networkError) {
      throw new ApiError({
        code: null,
        message: "Serverul nu răspunde. Verificați conexiunea și reîncercați.",
        field: null,
        status: 0,
      });
    }

    const rawText = await response.text();
    const data = rawText ? safeJsonParse(rawText) : null;

    if (!response.ok) {
      throw new ApiError(normalizeErrorBody(response.status, data));
    }
    return data;
  }

  function safeJsonParse(text) {
    try {
      return JSON.parse(text);
    } catch {
      return null;
    }
  }

  // `body` rămâne `undefined` dacă apelantul nu îl dă — ruta n-are niciun parametru
  // de body (ex. `PUT .../asked/{category}`) și nu trebuie să primească un corp inventat.
  const apiGet = (path) => apiRequest("GET", path);
  const apiPost = (path, body) => apiRequest("POST", path, body);
  const apiPut = (path, body) => apiRequest("PUT", path, body);
  const apiPatch = (path, body) => apiRequest("PATCH", path, body);
  const apiDelete = (path) => apiRequest("DELETE", path);

  // --------------------------------------------------------------------- bani

  /**
   * `19900` -> `"199,00 lei"`. Reproduce `money.format_ron` din backend, dar NU calculează
   * nimic: primește un întreg deja calculat de server și îl formatează pentru afișare.
   */
  function formatBani(bani) {
    if (typeof bani !== "number" || !Number.isFinite(bani)) {
      return "—";
    }
    const sign = bani < 0 ? "-" : "";
    const abs = Math.abs(Math.trunc(bani));
    const whole = Math.floor(abs / BANI_PER_LEU);
    const frac = abs % BANI_PER_LEU;
    return `${sign}${whole},${String(frac).padStart(2, "0")} lei`;
  }

  /** `{min_minutes, max_minutes}` -> `"30-40 minute"`. Fără eta -> textul indicat. */
  function formatEtaWindow(eta) {
    if (!eta) return "necunoscut";
    return `${eta.min_minutes}-${eta.max_minutes} minute`;
  }

  // --------------------------------------------------------------------- text sigur

  function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // --------------------------------------------------------------------- jurnal de erori

  /**
   * Creează un jurnal de erori vizibil într-un container dat. Fiecare eroare e o
   * intrare distinctă, cu buton de închidere — nimic nu se pierde tăcut în consolă.
   */
  function createErrorLog(container) {
    function report(issue) {
      const entry = document.createElement("div");
      entry.className = "error-entry";
      const codeText = issue.code ? escapeHtml(issue.code) : `HTTP ${issue.status}`;
      const fieldText = issue.field ? `<span class="error-field">câmp: ${escapeHtml(issue.field)}</span>` : "";
      entry.innerHTML = `
        <div class="error-entry-head">
          <span class="error-code">${codeText}</span>
          ${fieldText}
          <button type="button" class="error-dismiss" aria-label="Închide">×</button>
        </div>
        <div class="error-message">${escapeHtml(issue.message)}</div>
      `;
      entry.querySelector(".error-dismiss").addEventListener("click", () => entry.remove());
      container.prepend(entry);
      container.classList.add("has-errors");
    }

    function clear() {
      container.innerHTML = "";
      container.classList.remove("has-errors");
    }

    return { report, clear };
  }

  // --------------------------------------------------------------------- idempotență

  function genIdempotencyKey() {
    if (global.crypto && typeof global.crypto.randomUUID === "function") {
      return global.crypto.randomUUID();
    }
    // Fallback pentru browsere fără `crypto.randomUUID` (v4 aproximativ, suficient pentru test).
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  // --------------------------------------------------------------------- websocket comenzi

  /**
   * Conectare la `/ws/orders` cu reconectare automată (backoff exponențial, plafonat).
   * `onEvent(evenimentJson)` la fiecare mesaj; `onStatus("connecting"|"connected"|"disconnected")`
   * la fiecare schimbare de stare a conexiunii.
   */
  function connectOrdersSocket({ onEvent, onStatus }) {
    const MIN_DELAY_MS = 1000;
    const MAX_DELAY_MS = 15000;
    let delay = MIN_DELAY_MS;
    let socket = null;
    let closedByCaller = false;

    function scheduleReconnect() {
      if (closedByCaller) return;
      onStatus && onStatus("disconnected");
      setTimeout(open, delay);
      delay = Math.min(delay * 2, MAX_DELAY_MS);
    }

    function open() {
      if (closedByCaller) return;
      onStatus && onStatus("connecting");
      const protocol = global.location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${protocol}//${global.location.host}/ws/orders`);

      socket.addEventListener("open", () => {
        delay = MIN_DELAY_MS;
        onStatus && onStatus("connected");
      });
      socket.addEventListener("message", (messageEvent) => {
        const parsed = safeJsonParse(messageEvent.data);
        if (parsed) onEvent && onEvent(parsed);
      });
      socket.addEventListener("close", scheduleReconnect);
      socket.addEventListener("error", () => socket && socket.close());
    }

    open();

    return {
      close() {
        closedByCaller = true;
        if (socket) socket.close();
      },
    };
  }

  global.PizzaApi = {
    ApiError,
    apiGet,
    apiPost,
    apiPut,
    apiPatch,
    apiDelete,
    formatBani,
    formatEtaWindow,
    escapeHtml,
    createErrorLog,
    genIdempotencyKey,
    connectOrdersSocket,
  };
})(window);
