# Note de securitate — stare la finalul Fazei 1

Rulat deocamdată **exclusiv local**, un singur utilizator, fără trafic real. În acest
context riscurile de mai jos au impact zero. Ele devin reale în clipa în care API-ul e
accesibil de pe un al doilea dispozitiv — inclusiv un simplu ecran de bucătărie pe alt
calculator din rețeaua pizzeriei, cu mult înainte de telefonia din Faza 6.

## BLOCANT înainte de orice expunere în rețea

**Nu există autentificare sau autorizare.** Exclusă explicit din scopul Fazei 1, nu
uitată. Consecințele, cât timp lipsește:

- `session_id` (`S1`, `S2`, …) și `order_id` (`CMD-0001`, …) sunt secvențiale și
  previzibile. Cine ghicește un ID citește sau modifică apelul altui client.
- `GET /api/orders?view=driver` și `GET /api/orders/{id}` întorc telefon, nume și
  adresă completă cu interfon. Un script care numără de la `CMD-0001` extrage baza de
  clienți fără nicio breșă tehnică — ID-urile nu sunt un secret.
- `POST /api/orders/{id}/status` permite oricui să schimbe sau să anuleze statusul
  oricărei comenzi.
- `/ws/orders` acceptă orice conexiune. Difuzează doar `{order_id, status,
  fulfillment}`, dar oferă fluxul de ID-uri în timp real, amplificând punctele de mai sus.

**Minim necesar înainte de expunere:** ID-uri opace (UUID), autentificare per rol
(bucătărie / livrator / client) și o legătură verificată între apelant și sesiunea lui.

## Remediat în Faza 1

- Cursă cu pierdere de scriere pe coș: `SessionStore` serializează acum
  `citește → modifică → scrie` sub lock. Fără asta, două tool-call-uri simultane
  pierdeau un produs din comandă.
- `next_order_id` nu mai vine din `COUNT(*)` (recicla ID-uri după o ștergere și putea
  produce 500 la coliziune); acum derivă din maximul existent, sub lock, cu reîncercare.
- `EventHub.broadcast` itera lista mutată concurent și sărea conexiuni.
- Validare la graniță pe tot textul liber: lungimi maxime pe adresă, nume, telefon
  (cu format), cheie de idempotență, ingrediente scoase; plafon pe `qty` și pe numărul
  de linii din coș. Înainte, un body de câteva sute de KB la `resolve_address` bloca un
  worker prin backtracking de regex.
- Sesiunile în memorie au TTL de inactivitate și plafon dur (`SESSION_TTL_MINUTES`,
  `SESSION_MAX_ACTIVE`); `POST /api/sessions` nu mai poate epuiza memoria.
- Minimizare de date: `view=kitchen` nu mai conține telefon și adresă — bucătăria are
  nevoie de conținut, alergii și status. `GET /api/orders` cere acum `view` explicit.
- `/docs`, `/redoc`, `/openapi.json` închise dacă `ENABLE_DOCS != "1"`.

## Verificat curat

Fără secrete în cod; `.env` acoperit de `.gitignore`; nicio interogare SQL construită
prin concatenare (totul parametrizat prin SQLModel); fără path traversal — încărcătoarele
de fișiere nu primesc niciodată căi din HTTP; fără CORS permisiv; fără stack trace-uri
către client; fără date personale în loguri.

## Datorie asumată, de rezolvat înainte de clienți reali

- **Retenție GDPR:** comenzile cu telefon, nume și adresă rămân în `pizza_punto.db` la
  nesfârșit. E nevoie de politică de retenție și de un job de ștergere/anonimizare.
  Din Faza 3 se adaugă audio și transcrieri — date mult mai sensibile.
- **Idempotență doar pe `place_order`.** Un retry pe `add_item` dublează linia. Dedup-ul
  la nivel de tool-call e temă de Faza 2.
- **`allergy_note` și `notes` de adresă** nu sunt încă expuse de niciun endpoint. Când
  vor fi, au nevoie de plafoanele deja definite în `packages/domain/limits.py`.
