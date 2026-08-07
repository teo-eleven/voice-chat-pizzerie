"""Smoke end-to-end Faza 1: apel complet cu livrare + un apel cu ridicare."""

import os
import sys
import tempfile
from pathlib import Path

# rulează de oriunde: rădăcina proiectului e părintele lui scripts/
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # catalogul și config-urile se încarcă din căi relative la rădăcină
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mktemp(suffix='.db')}"

from fastapi.testclient import TestClient  # noqa: E402

from apps.api.main import app  # noqa: E402


def show(label, resp):
    print(f"\n--- {label} -> HTTP {resp.status_code}")
    print(resp.json())


with TestClient(app) as c:
    print("=" * 70)
    print("APEL 1 — LIVRARE")
    print("=" * 70)

    sid = c.post("/api/sessions").json()["session_id"]
    print(f"sesiune: {sid}")

    show("caut 'capriciosa'", c.get("/api/menu?query=capriciosa"))

    show("2x Capricciosa mare", c.post(f"/api/sessions/{sid}/items",
         json={"product_id": "PZ-002", "qty": 2, "size_code": "large"}))
    show("1x Quattro Stagioni medie fara ciuperci", c.post(f"/api/sessions/{sid}/items",
         json={"product_id": "PZ-003", "qty": 1, "size_code": "medium",
               "removed_ingredients": ["ciuperci"]}))
    show("2x sos picant", c.post(f"/api/sessions/{sid}/items",
         json={"product_id": "SO-001", "qty": 2}))
    show("1x Cola 2L", c.post(f"/api/sessions/{sid}/items",
         json={"product_id": "BT-002", "qty": 1}))
    show("1x Tiramisu", c.post(f"/api/sessions/{sid}/items",
         json={"product_id": "DS-001", "qty": 1}))

    show("livrare", c.put(f"/api/sessions/{sid}/fulfillment", json={"fulfillment": "delivery"}))
    show("adresa in afara zonei", c.post(f"/api/sessions/{sid}/address",
         json={"text": "Strada Aviatorilor 10"}))
    show("adresa ambigua", c.post(f"/api/sessions/{sid}/address",
         json={"text": "Strada Trandafirilor 5"}))
    show("adresa buna + detalii", c.post(f"/api/sessions/{sid}/address",
         json={"text": "Aleea Nucsoara 4, bloc 12, scara B, apartament 47"}))
    show("contact", c.put(f"/api/sessions/{sid}/contact",
         json={"phone": "0722334455", "name": "Teodor"}))
    show("plata cash", c.put(f"/api/sessions/{sid}/payment", json={"payment": "cash"}))

    summary = c.get(f"/api/sessions/{sid}/summary")
    print(f"\n--- SUMAR -> HTTP {summary.status_code}")
    data = summary.json()
    print("TEXT ROSTIT:")
    print(data.get("spoken_text") or data)

    placed = c.post(f"/api/sessions/{sid}/place", json={"idempotency_key": "call-1"})
    show("place", placed)
    oid = placed.json()["id"]

    replay = c.post(f"/api/sessions/{sid}/place", json={"idempotency_key": "call-1"})
    print(f"\n--- retry idempotent: {replay.json()['id']} == {oid} ? "
          f"{replay.json()['id'] == oid}")

    print(f"\nbucatarie: {[o['id'] for o in c.get('/api/orders?view=kitchen').json()]}")
    driver_before = [o["id"] for o in c.get("/api/orders?view=driver").json()]
    print(f"livrator (inainte de READY): {driver_before}")
    c.post(f"/api/orders/{oid}/status", json={"status": "in_kitchen"})
    c.post(f"/api/orders/{oid}/status", json={"status": "ready"})
    print(f"livrator (dupa READY): {[o['id'] for o in c.get('/api/orders?view=driver').json()]}")
    show("PICKED_UP pe o livrare (trebuie 422)",
         c.post(f"/api/orders/{oid}/status", json={"status": "picked_up"}))

    print("\n" + "=" * 70)
    print("APEL 2 — RIDICARE")
    print("=" * 70)
    sid2 = c.post("/api/sessions").json()["session_id"]
    c.post(f"/api/sessions/{sid2}/items",
           json={"product_id": "PZ-001", "qty": 1, "size_code": "small"})
    show("ridicare", c.put(f"/api/sessions/{sid2}/fulfillment", json={"fulfillment": "pickup"}))
    c.put(f"/api/sessions/{sid2}/contact", json={"phone": "0733445566", "name": "Ana"})
    c.put(f"/api/sessions/{sid2}/payment", json={"payment": "card_on_delivery"})
    s2 = c.get(f"/api/sessions/{sid2}/summary").json()
    print("\nSUMAR RIDICARE (ETA trebuie sa reflecte coada de la apelul 1):")
    print(s2.get("spoken_text") or s2)
    placed2 = c.post(f"/api/sessions/{sid2}/place", json={"idempotency_key": "call-2"})
    show("place ridicare", placed2)
    oid2 = placed2.json()["id"]
    print(f"\nbucatarie: {[o['id'] for o in c.get('/api/orders?view=kitchen').json()]}")
    print(f"livrator (ridicarea NU trebuie sa apara): "
          f"{[o['id'] for o in c.get('/api/orders?view=driver').json()]}")
    print(f"ridicarea {oid2} e in lista livratorului? "
          f"{oid2 in [o['id'] for o in c.get('/api/orders?view=driver').json()]}")
