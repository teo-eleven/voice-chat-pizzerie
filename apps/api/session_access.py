"""Accesul la sesiunea de apel: citire si mutatie atomica.

Sta separat de rute pentru ca il folosesc si rutele (`routes_session`) si logica de
plasare (`placement`). Daca ar trai in oricare dintre ele, celalalt ar trebui sa-l
importe si am inchide un ciclu de import.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, Request

from .sessions import CallSession, SessionStore


def require_call_session(request: Request, sid: str) -> CallSession:
    store: SessionStore = request.app.state.sessions
    call_session = store.get(sid)
    if call_session is None:
        raise HTTPException(status_code=404, detail=f"Sesiunea „{sid}” nu exista.")
    return call_session


def mutate(
    request: Request, sid: str, fn: Callable[[CallSession], CallSession]
) -> CallSession:
    """Aplica `fn` atomic prin `SessionStore.update` (citire -> aplicare -> scriere
    sub lock), ca doua request-uri paralele pe aceeasi sesiune sa nu se piarda."""
    store: SessionStore = request.app.state.sessions
    updated = store.update(sid, fn)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Sesiunea „{sid}” nu exista.")
    return updated
