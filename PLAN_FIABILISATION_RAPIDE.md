# Plan de fiabilisation rapide et exhaustif

## Objectif
Stabiliser l'application en **2 semaines** avec un plan priorisé par risque, tout en gardant une capacité de livraison fonctionnelle.

---

## 1) Priorités (ordre d'exécution)

### P0 — Stopper les incidents (J1 à J3)
1. **Baseline de santé applicative**
   - Vérifier automatiquement: boot Flask, DB, endpoint `/healthz`, présence des rôles RBAC critiques, présence des comptes de test.
   - Livrable: script de smoke checks exécutable en local/CI.
2. **Sécurisation du démarrage**
   - Échouer explicitement si migration DB échoue (déjà partiellement en place) et journaliser de façon actionnable.
   - Ajouter un mode "diagnostic" documenté pour l'exploitation.
3. **Compte admin de secours maîtrisé**
   - Formaliser la procédure de création/réparation (`bootstrap_user.py`) avec vérification post-op.

### P1 — Réduire la dette qui casse en prod (J4 à J7)
4. **Tests de non-régression ciblés**
   - Priorité aux flux critiques: login, dashboard, CRUD participants/projets, permissions RBAC.
5. **Durcissement des scripts d'exploitation**
   - Scripts idempotents: création users, backup/restore, migration.
   - Codes retour explicites non-0 en cas d'erreur.
6. **Normalisation des rôles et permissions**
   - Éviter les variantes historiques au runtime (alias centralisés + validation).

### P2 — Fiabilité opérationnelle (Semaine 2)
7. **Observabilité minimum**
   - Logs structurés (niveau, route, utilisateur, corrélation).
   - Tableau de bord d'erreurs (même simple via fichiers + rotation).
8. **Plan de reprise**
   - Vérifier régulièrement `backup -> restore -> smoke checks`.
9. **Pré-prod légère**
   - Environnement miroir avec DB de test + jeu de données anonymisé.

---

## 2) Plan exhaustif par axes

## Axe A — Qualité code & tests
- Ajouter une suite de smoke tests systématiques (boot + santé + RBAC).
- Ajouter des tests unitaires sur:
  - mapping des rôles (direction/directeur/directrice),
  - permissions de base,
  - services à logique métier (exports, stats).
- Ajouter une étape de checks syntaxiques sur les scripts utilitaires.

## Axe B — Données & migrations
- Imposer un **check DB preflight** avant scripts sensibles.
- Systématiser les migrations Alembic pour les changements de schéma.
- Ajouter une checklist "migration safe" (backup, upgrade, rollback).

## Axe C — Exploitation
- Établir un runbook incidents: 
  - application KO,
  - migration cassée,
  - perte de mot de passe admin,
  - corruption de données.
- Exiger des commandes reproductibles (pas d'action manuelle implicite).

## Axe D — Sécurité & accès
- Politique mot de passe minimale pour comptes critiques.
- Vérification périodique des rôles sensibles (`admin_tech`, `direction`).
- Vérification des secrets de prod (`SECRET_KEY`, DB URI).

## Axe E — UX de robustesse
- Messages d'erreur actionnables côté UI (pas de stacktrace brute).
- Éviter les pertes de saisie: autosave ou warning avant quitter.
- Validation formulaire cohérente front/back.

---

## 3) Application immédiate démarrée dans ce lot

### ✅ Action lancée maintenant
- Ajout d'un script `tools/run_reliability_checks.py` qui exécute des **smoke checks de fiabilité**:
  - boot de l'application,
  - accès à `/healthz`,
  - vérification tables RBAC minimales,
  - vérification des rôles critiques,
  - option de contrôle des deux users de test (`--require-test-users`).

### Résultat attendu
- Détection rapide des régressions bloquantes avant déploiement.
- Base exploitable pour un futur job CI/CD.

---

## 4) Cadence de mise en œuvre proposée

- **Quotidien (15 min)**: exécuter les smoke checks avant push.
- **2 fois/semaine**: revue incidents + corrections P0/P1.
- **Hebdo**: exercice backup/restore avec vérification fonctionnelle.

---

## 5) Définition de "fiable" (Done)

L'application est considérée "fiabilisée v1" lorsque:
1. Les smoke checks passent en continu sur environnement de dev et pré-prod.
2. Les rôles critiques et comptes d'administration sont vérifiés automatiquement.
3. La procédure de reprise (backup/restore) est testée au moins 1 fois par semaine.
4. Les incidents P0 sont traçables, reproductibles et documentés.
