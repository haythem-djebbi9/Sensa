"""Outils IA partagés : clients des fournisseurs et traitement de texte."""


class AIError(Exception):
    """Un appel à un service d'IA a échoué (réseau, quota, réponse invalide...)."""


class AIUnavailable(AIError):
    """Aucun fournisseur n'est configuré ou installé pour cette tâche."""
