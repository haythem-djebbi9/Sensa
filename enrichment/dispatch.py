"""Envoi des tâches IA (module 3).

- Avec Redis (production) : la tâche part dans la file Celery, un worker la traite.
- Sans Redis (développement) : Celery est en mode « eager ». Pour ne jamais faire attendre
  l'utilisateur, la tâche est alors exécutée dans un thread d'arrière-plan
  (SENSA_ASYNC_DEV=1, par défaut), et les relances respectent leur délai.
- En test (SENSA_ASYNC_DEV=0) : exécution synchrone, déterministe.
"""

import logging
import threading

from django.conf import settings
from django.db import close_old_connections, connection

logger = logging.getLogger("sensa.ai")


def _run_in_thread(task, args):
    close_old_connections()
    try:
        task.apply(args=args)
    except Exception:
        logger.exception("Tâche %s en échec dans le thread local", task.name)
    finally:
        connection.close()


def enqueue(task, args, countdown=None):
    """Planifie `task(*args)`, éventuellement après `countdown` secondes."""
    if settings.CELERY_TASK_ALWAYS_EAGER and getattr(settings, "SENSA_ASYNC_DEV", False):
        if countdown:
            timer = threading.Timer(countdown, _run_in_thread, args=(task, list(args)))
            timer.daemon = True
            timer.start()
        else:
            threading.Thread(target=_run_in_thread, args=(task, list(args)), daemon=True).start()
        return
    if countdown:
        task.apply_async(args=list(args), countdown=countdown)
    else:
        task.apply_async(args=list(args))
