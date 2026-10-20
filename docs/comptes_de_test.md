# Comptes de test Sensa

Créés (ou réinitialisés) avec :

```bash
.venv\Scripts\python manage.py create_test_accounts
```

Connexion sur http://127.0.0.1:8000 avec le nom d'utilisateur **ou** l'e-mail (`<nom>@sensa.test`).

| Nom d'utilisateur | Mot de passe | Rôle | Pour tester |
|---|---|---|---|
| `test_membre` | `Membre#2026` | Membre | Publier, commenter, aimer, rechercher, notifications |
| `test_moderateur` | `Modo#2026` | Modérateur | File de modération, tableau de bord du pipeline IA |
| `test_admin` | `Admin#2026` | Administrateur | Paramètres, comptes et rôles, configuration du pipeline, `/admin/` |
| `hiba` | `Hiba#Module1` | Administrateur | Module 1 · Publications |
| `mednour` | `MedNour#Module2` | Administrateur | Module 2 · Interactions sociales |
| `chedly` | `Chedly#Module4` | Administrateur | Module 4 · Modération et back-office |
| `taieb` | `Taieb#Module5` | Administrateur | Module 5 · Fil, recherche et recommandations |

Le compte `haythem` est un compte personnel existant : la commande ne le modifie pas.

Comptes de démonstration (`python manage.py seed_demo`) : `admin`, `camille`, `lina`, `maya`,
`noah`, `elena`, `karim`, mot de passe `SensaDemo2026!`.

> Comptes destinés au développement local uniquement : ne pas utiliser ces mots de passe en production.
