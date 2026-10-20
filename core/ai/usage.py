"""Collecte de l'usage des appels IA (fournisseur, modèle, jetons, latence).

Le runner du module 3 ouvre une collecte autour de chaque tentative ; les clients
Groq et Claude y enregistrent leurs appels. Le journal des tentatives (TentativeIA)
sait ainsi quel modèle a répondu, en combien de temps et pour combien de jetons.
"""

import contextvars
from contextlib import contextmanager

_calls = contextvars.ContextVar("sensa_ai_calls", default=None)


@contextmanager
def collect():
    calls = []
    token = _calls.set(calls)
    try:
        yield calls
    finally:
        _calls.reset(token)


def record(provider, model, tokens=0, latency_ms=0):
    calls = _calls.get()
    if calls is not None:
        calls.append({"provider": provider, "model": model, "tokens": int(tokens or 0), "latency_ms": int(latency_ms)})
