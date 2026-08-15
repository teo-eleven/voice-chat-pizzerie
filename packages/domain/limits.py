"""Plafoane de siguranță.

Nu sunt reguli comerciale, sunt limite care împiedică o comandă absurdă să ajungă la
bucătărie și un apelant rău-intenționat să umple memoria. Comenzile mari legitime
(catering, birouri) se escaladează la om — nu se rezolvă prin ridicarea plafoanelor.
"""

from __future__ import annotations

#: Câte unități se pot cere pe o singură linie de coș.
MAX_QTY_PER_LINE = 50

#: Câte linii distincte poate avea un coș.
MAX_LINES_PER_CART = 40

#: Lungimi maxime pentru textul liber primit de la client.
MAX_ADDRESS_TEXT_LEN = 500
MAX_NAME_LEN = 80
MAX_PHONE_LEN = 20
MAX_NOTES_LEN = 300
MAX_ALLERGY_NOTE_LEN = 300
MAX_IDEMPOTENCY_KEY_LEN = 100

#: Lungimi maxime pentru datele de adresă venite din surse externe (OpenStreetMap).
#: Tot ce scrie un cartograf într-o etichetă OSM ajunge în fișierele din `data/`, apoi
#: în memoria serverului la fiecare pornire și în răspunsurile API. Cea mai lungă
#: stradă reală din Suceava are 40 de caractere, iar cel mai lung număr 6 — plafoanele
#: de mai jos lasă loc de trei ori pe atât și opresc o etichetă absurdă înainte să
#: intre în date.
MAX_STREET_NAME_LEN = 120
MAX_HOUSE_NUMBER_LEN = 24
MAX_POSTCODE_LEN = 16

#: Peste atâtea unități pe linie sau linii în coș, e o comandă de tip catering.
#:
#: SCHELET FAZA 5, încă neconectat. `docs/PLAN.md` cere ca o comandă mare să fie
#: escaladată la un operator uman, nu refuzată — iar escaladarea nu există încă
#: (vezi `EscalationReason.LARGE_ORDER`). Pragul stă aici ca decizia să fie luată o
#: singură dată și într-un singur loc, nu împrăștiată prin cod când vine faza.
LARGE_ORDER_QTY_THRESHOLD = 20
