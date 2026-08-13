"""Hub WebSocket pentru evenimentele de comanda (dashboard bucatarie/livrator).

O conexiune cazuta nu trebuie sa arunce exceptii in restul aplicatiei: emiterea
catre clienti deconectati e ignorata silentios, conexiunea e scoasa din lista.
"""

from __future__ import annotations

from typing import Any

from fastapi import WebSocket, WebSocketDisconnect


class EventHub:
    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Trimite evenimentul catre toti clientii conectati; ignora esecurile individuale.

        Itereaza pe o copie (`tuple(...)`): `disconnect` poate muta lista live din alt
        task cat timp bucla e suspendata la `await`, iar o iterare directa pe lista ar
        sari peste conexiunea urmatoare celei scoase, pierzand un eveniment.
        """
        dead: list[WebSocket] = []
        for connection in tuple(self._connections):
            try:
                await connection.send_json(event)
            except Exception:
                dead.append(connection)
        for connection in dead:
            self.disconnect(connection)


async def handle_connection(websocket: WebSocket, hub: EventHub) -> None:
    """Accepta conexiunea si o tine vie pana la deconectare, fara sa propage erori."""
    await hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(websocket)
