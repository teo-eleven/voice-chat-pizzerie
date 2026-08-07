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

#: Peste atâtea unități pe linie sau linii în coș, e o comandă de tip catering.
LARGE_ORDER_QTY_THRESHOLD = 20
