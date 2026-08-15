/**
 * api.js — helper partajat de cele trei ecrane (index / kitchen / driver).
 *
 * Scop, strict:
 *   1. `apiRequest`/`apiGet`/`apiPost`/`apiPut`/`apiPatch`/`apiDelete` — fetch spre `/api/...`,
 *      cu parsare uniformă a erorilor (422 de domeniu, 404 simplu, 422 de validare Pydantic).
 *   2. `formatBani` — formatare de afișare a unui întreg în bani, deja calculat de server.
 *      Nu adună, nu scade, nu calculează niciun total — doar formatează un număr dat.
 *   3. `createToaster` — pop-up-uri pentru toate mesajele și erorile, într-un singur
 *      loc pe ecran. Nimic tehnic nu ajunge la client: codul, câmpul și statutul HTTP
 *      merg în consolă, mesajul în română merge pe ecran.
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
      /** Detaliul pentru programator (cod, câmp, mesaj brut). Nu se afișează. */
      this.technical = issue.technical ?? null;
    }
  }

  /**
   * Ce vede clientul când validarea Pydantic respinge cererea.
   *
   * Mesajul brut al lui Pydantic e în engleză și numește câmpul din API
   * („body.qty: Input should be greater than 0”). O validare picată aici înseamnă
   * că ecranul a trimis ceva ce n-ar fi trebuit să trimită — e un bug al nostru, nu
   * o greșeală a clientului, iar el n-are ce face cu numele câmpului. Îi spunem în
   * română ce s-a întâmplat; detaliul tehnic rămâne în consolă, pentru noi.
   */
  const VALIDATION_MESSAGES = {
    qty: "Cantitatea cerută nu este validă.",
    text: "Textul introdus nu este valid.",
    phone: "Numărul de telefon nu este valid.",
    name: "Numele introdus nu este valid.",
    product_id: "Produsul cerut nu este valid.",
    size_code: "Mărimea aleasă nu este validă.",
    payment: "Metoda de plată aleasă nu este validă.",
    fulfillment: "Tipul de preluare ales nu este valid.",
    idempotency_key: "Comanda nu a putut fi trimisă. Reîncercați.",
  };

  const GENERIC_VALIDATION_MESSAGE = "Datele trimise nu sunt valide. Verificați și reîncercați.";

  /** Ce vede clientul când cade rețeaua sau serverul răspunde cu ceva neașteptat. */
  const STATUS_MESSAGES = {
    404: "Nu am găsit ce ați cerut. Reîncărcați pagina și încercați din nou.",
    409: "Comanda a fost modificată între timp. Reîncărcați pagina.",
    429: "Prea multe cereri într-un timp scurt. Așteptați câteva secunde.",
    500: "A apărut o problemă la server. Încercați din nou în câteva momente.",
    502: "Serverul nu răspunde acum. Încercați din nou în câteva momente.",
    503: "Serverul nu răspunde acum. Încercați din nou în câteva momente.",
  };

  const FALLBACK_MESSAGE = "Ceva nu a mers. Încercați din nou.";

  /**
   * Normalizează formele posibile de corp de eroare ale API-ului.
   *
   * `message` e mereu o propoziție în română, gata de arătat clientului. `technical`
   * ține codul, câmpul și statutul HTTP — merg în consolă, niciodată pe ecran: un
   * „HTTP 422 / invalid_qty / câmp: qty" nu-i spune clientului nimic, dar îl sperie.
   */
  function normalizeErrorBody(status, data) {
    if (data && typeof data === "object" && "code" in data && "message" in data) {
      // Forma domeniului: {code, message, field}. Mesajul e deja scris în română,
      // pentru client, de backend — se arată exact cum vine.
      return { code: data.code, message: data.message, field: data.field ?? null, status };
    }
    if (data && typeof data.detail === "string") {
      // HTTPException simplu (ex: sesiune inexistentă) — {detail: "..."}.
      return { code: null, message: data.detail, field: null, status };
    }
    if (data && Array.isArray(data.detail)) {
      // 422 de validare Pydantic, înainte să ajungă la ruta de domeniu.
      const first = data.detail[0] ?? {};
      const field = Array.isArray(first.loc) ? String(first.loc[first.loc.length - 1]) : null;
      const technical = data.detail
        .map((item) => {
          const loc = Array.isArray(item.loc) ? item.loc.join(".") : "";
          return loc ? `${loc}: ${item.msg}` : item.msg;
        })
        .join(" | ");
      return {
        code: "validation_error",
        message: VALIDATION_MESSAGES[field] || GENERIC_VALIDATION_MESSAGE,
        field,
        status,
        technical,
      };
    }
    return {
      code: null,
      message: STATUS_MESSAGES[status] || FALLBACK_MESSAGE,
      field: null,
      status,
      technical: `HTTP ${status}`,
    };
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

  /**
   * Numărul rostit al comenzii: „numărul comenzii 7”.
   *
   * Pe ecrane apare ăsta, nu `order.id`. Id-ul (`CMD-0142`) e identitatea internă,
   * unică peste tot istoricul; numărul e cel pe care clientul îl reține și bucătarul
   * îl strigă, și repornește de la 1 în fiecare zi. Comenzile scrise înainte de
   * numerotare n-au niciun număr — pentru ele rămâne id-ul, ca să nu apară „0”.
   */
  function formatOrderNumber(order) {
    const number = order && order.daily_number;
    if (typeof number !== "number" || number < 1) {
      return order && order.id ? String(order.id) : "—";
    }
    return `numărul comenzii ${number}`;
  }

  /** Aceeași etichetă, cu majusculă la început, pentru început de propoziție sau titlu. */
  function formatOrderNumberCapitalized(order) {
    const label = formatOrderNumber(order);
    return label.charAt(0).toUpperCase() + label.slice(1);
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

  // --------------------------------------------------------------------- pop-up-uri

  /**
   * Mesajele către client apar ca pop-up, într-un singur loc pe ecran.
   *
   * De ce pop-up și nu text lângă butonul apăsat: mesajul trebuie văzut chiar dacă
   * omul se uită în altă parte a paginii, și trebuie să dispară singur după ce l-a
   * citit — un text rămas pe ecran devine, peste câteva acțiuni, o minciună despre
   * starea curentă a comenzii.
   *
   * Ce NU ajunge niciodată aici: coduri de eroare, nume de câmpuri, statut HTTP.
   * Alea se scriu în consolă (`console.debug`), unde le caută programatorul.
   */
  const TOAST_DURATIONS_MS = { success: 4000, info: 5000, warning: 7000, error: 9000 };
  const MAX_VISIBLE_TOASTS = 4;
  const TOAST_EXIT_MS = 200;
  const TOAST_TITLES = {
    success: "Gata",
    info: "Informație",
    warning: "Atenție",
    error: "A apărut o problemă",
  };

  function createToaster() {
    let stack = null;
    const shown = new Map();

    function ensureStack() {
      if (stack && stack.isConnected) return stack;
      stack = document.createElement("div");
      stack.className = "toast-stack";
      // `polite`, nu `assertive`: cititorul de ecran termină propoziția curentă
      // înainte să anunțe mesajul, în loc s-o taie la mijloc.
      stack.setAttribute("aria-live", "polite");
      stack.setAttribute("role", "status");
      document.body.appendChild(stack);
      return stack;
    }

    function dismiss(toast) {
      if (!toast || toast.dataset.leaving === "1") return;
      toast.dataset.leaving = "1";
      toast.classList.add("toast--leaving");
      const key = toast.dataset.key;
      if (key) shown.delete(key);
      global.setTimeout(() => toast.remove(), TOAST_EXIT_MS);
    }

    function show(kind, message, options) {
      const text = String(message || "").trim();
      if (!text) return null;
      const settings = options || {};
      const container = ensureStack();

      // Același mesaj apăsat de două ori la rând nu se dublează pe ecran: îi
      // repornim doar cronometrul, ca omul să vadă că a fost înregistrat.
      const key = `${kind}:${text}`;
      const existing = shown.get(key);
      if (existing && existing.isConnected) {
        restartTimer(existing, kind, settings);
        return existing;
      }

      const toast = document.createElement("div");
      toast.className = `toast toast--${kind}`;
      toast.dataset.key = key;
      toast.innerHTML = `
        <div class="toast-body">
          <span class="toast-title">${escapeHtml(settings.title || TOAST_TITLES[kind])}</span>
          <span class="toast-message">${escapeHtml(text)}</span>
        </div>
        <button type="button" class="toast-close" aria-label="Închide mesajul">×</button>
      `;
      toast.querySelector(".toast-close").addEventListener("click", () => dismiss(toast));

      container.appendChild(toast);
      shown.set(key, toast);
      // Cele mai vechi pleacă, dar se numără doar cele care nu sunt deja în curs de
      // ieșire: `dismiss` scoate elementul din DOM abia după animație, deci o buclă
      // care ar aștepta scăderea lui `children.length` s-ar învârti la nesfârșit și
      // ar îngheța pagina exact când apar multe mesaje deodată.
      const live = Array.prototype.filter.call(
        container.children,
        (item) => item.dataset.leaving !== "1"
      );
      live.slice(0, Math.max(0, live.length - MAX_VISIBLE_TOASTS)).forEach(dismiss);
      restartTimer(toast, kind, settings);
      return toast;
    }

    function restartTimer(toast, kind, settings) {
      global.clearTimeout(Number(toast.dataset.timer));
      if (settings.sticky) return;
      const delay = settings.durationMs || TOAST_DURATIONS_MS[kind] || TOAST_DURATIONS_MS.info;
      toast.dataset.timer = String(global.setTimeout(() => dismiss(toast), delay));
    }

    function reportError(error, fallbackMessage) {
      // Orice ajunge aici e afișat: o eroare tăcută e mai rea decât una urâtă.
      const isApiError = error instanceof ApiError;
      const message = (isApiError && error.message) || fallbackMessage || FALLBACK_MESSAGE;
      if (global.console && typeof global.console.debug === "function") {
        global.console.debug("[pizza-punto]", {
          message,
          code: isApiError ? error.code : null,
          field: isApiError ? error.field : null,
          status: isApiError ? error.status : null,
          technical: isApiError ? error.technical : String((error && error.message) || error),
        });
      }
      return show("error", message);
    }

    return {
      success: (message, options) => show("success", message, options),
      info: (message, options) => show("info", message, options),
      warning: (message, options) => show("warning", message, options),
      error: (message, options) => show("error", message, options),
      reportError,
      clear() {
        if (stack) {
          // Cronometrele se anulează înainte de golire: altfel ar continua să se
          // declanșeze pe noduri deja scoase din pagină. Nu strică nimic, dar e
          // lucru făcut degeaba, iar în alt context ar deveni o scurgere reală.
          Array.prototype.forEach.call(stack.children, (item) =>
            global.clearTimeout(Number(item.dataset.timer))
          );
          stack.innerHTML = "";
        }
        shown.clear();
      },
    };
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
    formatOrderNumber,
    formatOrderNumberCapitalized,
    escapeHtml,
    createToaster,
    genIdempotencyKey,
    connectOrdersSocket,
  };
})(window);
