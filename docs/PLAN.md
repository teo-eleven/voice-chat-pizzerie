# Pizzeria Punto — agent vocal de preluare comenzi

## Context

Vrem un agent conversațional care preia comenzi de pizza: clientul vorbește, agentul
notează, validează, confirmă, și trimite comanda la bucătărie și la livrator.

Proiect nou, de la zero, **100% local** pe `C:\Users\teodor.fotciuc\pizza-punto`.
Fără legătură cu `ecf-adm-expert`. Repo git local (`git init`), **fără remote** —
nimic nu se încarcă nicăieri.

Riscul #1 nu e AI-ul, e **acurateţea recunoașterii vocale pe română, pe adrese**.
De asta Faza 0 e o măsurătoare, nu cod de producție: dacă româna nu ține, arhitectura
se schimbă (confirmarea vizuală devine obligatorie, nu opțională).

Rezultatul urmărit: un apel de ~90 de secunde care produce o comandă corectă, validată
de server, vizibilă live în bucătărie și la livrator.

## Decizii deja luate (nu se redeschid)

| Decizie | Alegere |
|---|---|
| Canal v1 | Aplicație web, microfon în browser. Telefonia vine ulterior. |
| Arhitectură vocală | Pipeline STT → LLM → TTS (etape vizibile și înlocuibile) |
| Providers | Cloud plătit — calitate pe română |
| Ieșire comandă | Mini dashboard web: ecran bucătărie + ecran livrator |
| Găzduire | Local, fără deploy, fără git remote |

Confirmate prin verificare: Deepgram Nova-3 are română pe streaming; ElevenLabs
Flash v2.5 are română la ~75ms. LiveKit Agents ca framework — are pipeline
STT/LLM/TTS cu plugin-uri înlocuibile **și** SIP nativ, deci trecerea la telefonie
în Faza 6 nu cere rescriere.

## Principiul arhitectural (din care decurge tot)

**LLM-ul nu ține comanda în cap.** Coșul trăiește pe server. LLM-ul e interfața
conversațională peste un API de coș: interpretează, cheamă tool-uri, povestește
rezultatul. La fiecare tur primește starea reală a coșului injectată în context.

| Decizie | Cine o ia |
|---|---|
| Ce produse există, ce conțin, cât costă | Backend (catalog) |
| Total, reduceri, comandă minimă | Backend (pricing) |
| Livrăm la adresa asta? | Backend (poligon zonă) |
| ETA | Backend (capacitatea bucătăriei) |
| Stoc | Backend |
| Ce a vrut să spună clientul | LLM |
| Ce clarificare cere | LLM |
| Formularea răspunsului | LLM |
| Plasarea efectivă | Backend (re-validare completă de la zero) |

## Starea agentului: listă de bifat, nu șină de tren

Nu „la ce pas sunt", ci **ce am și ce îmi lipsește**. Două steaguri per categorie:
*am întrebat?* și *are ceva?* Întrebi doar unde ambele sunt goale. După un „nu",
categoria se închide definitiv.

```
produse       ✓/○
sosuri        întrebat? ○   are? ○
băuturi       întrebat? ○   are? ○
desert        întrebat? ○   are? ○
tip livrare   ○   ← se află DEVREME (după produsele principale)
adresă        ○   ← doar la livrare; verificare zonă imediat
telefon       ○   ← v1: tastat. Faza 6: din caller ID, doar confirmat
nume          ○   ← la final, prenumele e suficient
plată         ○   ← cash / card la livrare. NICIODATĂ card dictat.
```

Ordinea implicită a upsell-ului rămâne cea din discuție: **sosuri → băuturi → desert**.
Reordonarea singură care se aplică: **tipul de livrare + adresa se află înainte de
upsell**, ca să nu construim trei minute de comandă la o adresă unde nu livrăm.

## Arhitectura v1

```
Browser (mic + WebRTC)
   │
   ▼
apps/agent  — LiveKit Agent (Python)
   VAD + turn detection → STT (Deepgram) → LLM (Claude + tools) → TTS (ElevenLabs)
   │  barge-in: oprește TTS instant ȘI taie din istoric ce n-a fost auzit
   ▼ tool calls (HTTP)
apps/api    — FastAPI + SQLite
   catalog · cart · pricing · zonă livrare · capacitate/ETA · orders · WebSocket
   │
   ▼ evenimente WebSocket
apps/web    — /order (client)  ·  /kitchen  ·  /driver
```

Dashboard-urile sunt doar consumatori de evenimente. La reload își reconstruiesc
starea din DB, nu din ce au prins pe WebSocket. Sursa de adevăr = SQLite.

## Contractul de tools (interfața LLM ↔ backend)

```
search_menu(query)                      → produse reale, cu mărimi și preț
add_item(product_id, size, qty, extras) → coș recalculat de backend
update_item(line_id, ...)               → modificări (fără ceapă, altă mărime)
remove_item(line_id)
set_fulfillment("delivery" | "pickup")
resolve_address(text_brut)              → candidați + verificare zonă + minim
set_contact(phone, name)
set_payment("cash" | "card")
answer_question(intrebare)              → FAQ/meniu; ieșirea din script
get_order_summary()                     → TEXT CANONIC: produse, total, ETA
place_order(idempotency_key)            → doar după confirmare explicită
escalate_to_human(motiv)
```

Reguli: LLM-ul nu calculează niciodată un preț. `place_order` re-validează tot de la
zero (stoc, zonă, preț, minim) și e idempotent — un retry de rețea nu produce
comandă dublă.

---

## Fazele

### Faza 0 — Măsurătoare STT română · GO/NO-GO (~½ zi)

Independentă de framework. Decide arhitectura, deci merge prima.

- `data/recordings/` — 20-30 clipuri reale: comenzi complete, adrese cu bloc/scară/
  apartament, șiruri de cifre.
- Două condiții per clip: **wideband** (16 kHz) și **simulare telefon**
  (downsample 8 kHz + round-trip μ-law prin ffmpeg) — ca să știi de acum ce te
  așteaptă în Faza 6.
- Rulează prin Deepgram Nova-3 (`ro`) și cel puțin un alternativ.
- Metrici: WER global, dar decisiv e **acurateţea pe entități**, separat pe
  *nume produs* / *stradă+număr* / *cifre*.

**Livrabil:** `evals/reports/stt-bakeoff.md` — tabel comparativ + provider ales.
**Prag:** entități-adresă ≥ 90% pe wideband. Sub asta, confirmarea vizuală devine
obligatorie prin arhitectură (o recomand oricum).

### Faza 1 — Domeniul + API, zero voce (~2-3 zile)

Partea care trebuie să fie corectă indiferent de AI. Testabilă determinist.

- `packages/domain/` — modele Pydantic v2, `catalog.py`, `pricing.py`, `cart.py`,
  `delivery_zone.py`, `capacity.py` (ETA), `order_state.py`. Funcții pure.
- `apps/api/` — FastAPI + SQLite (SQLModel), endpoint-uri pentru fiecare tool,
  `/ws/orders`, tranziții de status.
- `data/menu.seed.json` — meniu Pizzeria Punto: pizze cu 3 mărimi, sosuri,
  băuturi, deserturi.
- **ETA din capacitate, nu din numărul de comenzi:** sloturi de cuptor × timp de
  coacere + preparare, peste munca rămasă în coadă. Ridicarea și livrarea consumă
  aceeași coadă. Rezultatul se dă ca **interval rotunjit în sus**.

**Teste:** unit pe pricing, ETA, poligon zonă, tranziții de status. Prag 80%.

### Faza 2 — Agentul pe text (~2-3 zile)

Aici se rezolvă toată dificultatea conversațională, **fără** complexitatea audio.
Iterezi pe prompt în secunde, nu în apeluri de trei minute.

- `apps/agent/checklist.py` — starea de mai sus, cu steagurile *întrebat/are*.
- `apps/agent/prompts/` — system prompt versionat.
- `apps/agent/cli.py` — chat text în terminal, aceleași tool-uri ca la voce.
- `evals/scenarios/*.json` + `evals/run_evals.py` — scenarii „golden": input-uri de
  conversație → comanda finală așteptată. **Rulate înainte de orice modificare de prompt.**

Scenarii obligatorii, direct din cazurile discutate: comandă dintr-o suflare (upsell
sărit corect), aritmetică nepotrivită („trei sucuri, cola, fanta"), cantitate înaintea
produsului („două picante și unul dulce"), modificare după sumar, anulare la final,
adresă în afara zonei, produs epuizat la mijloc, întrebare off-script, alergie,
comandă mare → escaladare, apel dublu.

### Faza 3 — Vocea în browser (~2-3 zile)

- `apps/agent/voice.py` — LiveKit Agents: Deepgram → agentul din Faza 2 → ElevenLabs.
- `apps/web/order.html` — buton „Sună", transcript live, **sumarul afișat vizual**
  în paralel cu citirea lui.
- Barge-in + turn detection semantic (nu doar timeout de silence).

**Metrică:** latență p50/p95 de la sfârșitul vorbirii la primul audio. Țintă p50 < 1.2s.

### Faza 4 — Dashboard bucătărie + livrator (~1-2 zile)

- `apps/web/kitchen.html` — **toate** comenzile (ridicare și livrare), cronometru,
  buton „gata", alergii evidențiate.
- `apps/web/driver.html` — **doar** livrările, **doar** de la status `READY`.
- Ridicarea nu apare niciodată la livrator; clientul primește intervalul de ridicare.

```
NEW → IN_KITCHEN → READY → ┬ livrare:  ASSIGNED → OUT → DELIVERED
                           └ ridicare: PICKED_UP
```

### Faza 5 — Plasa de siguranță (~2 zile)

Escaladare la om (confidence mic de două ori pe același câmp, client nervos,
reclamație, alergie, comandă mare). Client recunoscut → scurtătura „aceeași adresă?".
Notificare că e AI + consimțământ de înregistrare la deschidere. Timeout-uri blânde
(vorbitor secundar în cameră ≠ input). Detecție apel dublu. Coș care supraviețuiește
unei deconectări. Circuit breaker pe providers. Program de funcționare.

**Log per apel: transcript + toate tool call-urile.** Când o comandă iese greșit,
știi exact unde s-a rupt — STT, LLM sau validare. Aceste înregistrări devin setul
de evaluare.

### Faza 6 — Telefonie reală (ulterior, când v1 e solid)

LiveKit SIP + număr RO. Telefonul din caller ID, doar confirmat. SMS de confirmare
(închide bucla dacă apelul cade după plasare). Re-măsurare latență și WER pe 8 kHz real.
Fallback obligatoriu: dacă stack-ul cade, apelul se rutează la telefonul normal.

---

## Structura

```
C:\Users\teodor.fotciuc\pizza-punto\
  apps/
    api/         # FastAPI: endpoint-uri tool, WebSocket, persistență
    agent/       # checklist, prompts, cli.py (text), voice.py (LiveKit)
    web/         # order.html, kitchen.html, driver.html
  packages/
    domain/      # pur: catalog, pricing, cart, zone, capacity, order_state
  evals/
    scenarios/   # conversații golden
    reports/
  data/
    menu.seed.json
    recordings/  # audio Faza 0
  tests/
  .env.example   # NUMAI chei goale
  .gitignore     # .env, *.db, recordings/, __pycache__
```

## Stack și comenzi

Python 3.12 prin **`uv`**. Atenție: `python` din PATH e stub-ul Microsoft Store și nu
funcționează — toate comenzile prin `uv run`, niciodată `python` direct.

```powershell
uv init ; uv python pin 3.12
uv add fastapi uvicorn pydantic sqlmodel anthropic "livekit-agents[deepgram,elevenlabs,anthropic,silero]" httpx
uv add --dev pytest pytest-asyncio pytest-cov ruff

uv run uvicorn apps.api.main:app --reload      # API + dashboard-uri
uv run python -m apps.agent.cli                # agent text (Faza 2)
uv run python -m apps.agent.voice dev          # agent vocal (Faza 3)
uv run pytest --cov=packages --cov=apps        # teste
uv run python evals/run_evals.py               # evals
```

Node 20 e disponibil dacă avem nevoie, dar frontend-ul v1 rămâne HTML + JS simplu,
fără build step.

## Prerechizite (blochează Faza 0)

Chei API în `.env`, niciodată în cod: `ANTHROPIC_API_KEY`, `DEEPGRAM_API_KEY`,
`ELEVENLABS_API_KEY`, plus `LIVEKIT_*` de la Faza 3 și un provider de geocoding
pentru adrese. Spune-mi care le ai și care lipsesc.

## Verificare

- **Faza 0:** tabelul din `stt-bakeoff.md` are numere pe ambele condiții audio.
- **Faza 1:** `uv run pytest` verde, coverage ≥ 80% pe `packages/domain`. Un total
  greșit sau un ETA calculat pe număr de comenzi = bug blocant.
- **Faza 2:** toate scenariile din `evals/scenarios/` trec. Un scenariu picat
  blochează avansul la voce.
- **Faza 3:** apel real din browser, comandă completă end-to-end, latență p50 sub
  țintă, barge-in funcțional (întrerupi agentul și se oprește imediat).
- **Faza 4:** o comandă cu livrare apare pe ambele ecrane la momentele corecte; una
  cu ridicare apare **doar** la bucătărie. Reload la dashboard reconstruiește starea.
- **Faza 5:** fiecare cale de eșec din tabelul de situații neprevăzute are un test
  sau un scenariu de eval.

Verificarea manuală în browser o faci tu — eu nu conduc Chrome.

## Ce NU intră în v1

Card dictat prin telefon (niciodată). Modificarea sau anularea unei comenzi deja
plasate. Reclamații. Comenzi programate la oră fixă. Catering / comenzi mari
(→ escaladare). Alocarea automată de livrator. Pizza jumătate-jumătate. Multi-tenant.
Deploy. Autentificare.

## Riscuri

| Risc | Mitigare |
|---|---|
| STT slab pe adrese în română | Faza 0 măsoară înainte de orice cod; confirmare vizuală obligatorie |
| Latență cumulată pe pipeline | Streaming pe fiecare etapă; măsurat în Faza 3; s2s ca plan B |
| Turn detection pe pauze de gândire | Detecție semantică, nu doar silence timeout |
| Produse/prețuri halucinate | Structural imposibil: catalog prin tools, calcul pe backend |
| Comandă dublă la retry | Cheie de idempotență pe `place_order` |
| ETA ratat → clienți nervoși | Capacitate reală, interval rotunjit în sus, plafon onest |
| Alergii | Marcaj vizibil în bucătărie + escaladare la om |
