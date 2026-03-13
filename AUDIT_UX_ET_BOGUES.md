# Audit rapide — bogues, UX et projection métier

## 1) L'application en bref

Cette application est un **ERP léger pour structures associatives / centres sociaux**:
- pilotage de projets, budgets AAP, subventions, dépenses ;
- gestion ateliers/sessions/émargement ;
- participants, partenaires, questionnaires d'impact ;
- modules pédagogie, stats et bilans.

Elle répond clairement à un besoin d'**unification des données terrain** (éviter les fichiers dispersés) et de **pilotage multi-métiers** dans un environnement non technique.

## 2) Bogues / points à risque identifiés

### B1 — création de compte “directeur” impossible en CLI (corrigé)
- Symptôme: `bootstrap_user.py` n'acceptait pas `directeur` ni `direction` dans `--role`.
- Impact: friction en test et incohérence avec les alias gérés dans le modèle `User.has_role`.
- Correctif: ajout des choix `direction` et `directeur`, normalisation vers `direction`.

### B2 — risque de confusion RBAC entre `directrice` et `direction`
- Constat: coexistence des codes de rôle dans le projet.
- Impact: ambiguïté dans la maintenance et l'administration des droits.
- Recommandation: converger vers un seul code canonique (`direction`) et garder les alias uniquement en lecture/compat.

### B3 — contrôle setup en `before_request` potentiellement coûteux
- Constat: `User.query.count()` est exécuté à chaque requête hors exceptions.
- Impact: overhead DB inutile à grande volumétrie.
- Recommandation: cache mémoire court (ex: 30-60s) ou drapeau d'initialisation persisté.

## 3) Améliorations UX prioritaires

1. **Onboarding par profil**
   - Présenter une page d'accueil différente selon le rôle (direction / secteur / admin).
2. **Réduction de la charge cognitive**
   - Masquer par défaut les modules non utilisés (navigation progressive).
3. **Feedback système explicite**
   - Harmoniser les messages de succès/erreur avec actions suivantes recommandées.
4. **Recherche transverse**
   - Barre de recherche globale (participants, projets, ateliers, partenaires).
5. **Tableaux orientés décision**
   - Sur dashboard: 5 indicateurs max + alertes actionnables (budget, absences, échéances).
6. **Accessibilité terrain**
   - Contrastes renforcés, tailles cliquables plus larges, optimisation mobile/tablette.

## 4) Maquette proposée (low-fidelity)

## Écran “Pilotage direction”

```text
+--------------------------------------------------------------------------------+
| [Logo] App Gestion                  Recherche globale [.....................]  |
|--------------------------------------------------------------------------------|
| Menu: Dashboard | Projets | Budget | Ateliers | Participants | Bilans | Admin |
|--------------------------------------------------------------------------------|
| KPI 1: Budget consommé | KPI 2: Taux présence | KPI 3: Projets en retard       |
|--------------------------------------------------------------------------------|
| Alertes prioritaires (max 5)                                               [>] |
| - Projet X: échéance subvention dans 7 jours                                  |
| - Atelier Y: taux d'absence > 35%                                             |
|--------------------------------------------------------------------------------|
| Vue synthèse par secteur                                                       |
| [Graph barres budget]     [Graph ligne présence]     [Graph radar impact]      |
|--------------------------------------------------------------------------------|
| Actions rapides                                                                 |
| [Créer un projet] [Saisir une dépense] [Planifier un atelier] [Exporter bilan]|
+--------------------------------------------------------------------------------+
```

## 5) Deux comptes de test créés

Script ajouté: `tools/create_test_users.py`

Comptes:
- Admin: `admin.test@mars.local` / `AdminMars123!`
- Direction: `directeur.test@mars.local` / `DirecteurMars123!`

> Script idempotent: relancer met à jour mot de passe + rôles sans dupliquer les comptes.

## 6) Niveau de complétude et avenir métier

### Ce qui est déjà solide
- Couverture métier large (finances + activité + impact + pédagogie).
- Base RBAC structurée.
- Bonne base pour industrialiser le déploiement (migrations, healthcheck, config env).

### Ce qui manque pour un “outil métier mature”
- Standard QA (tests automatiques, scénarios de non-régression).
- Observabilité (journaux structurés, métriques, audit trail des actions critiques).
- Gouvernance data (qualité, doublons, traçabilité import/export).

### Vision d'évolution
- Court terme: stabiliser UX + fiabilité (moins de friction, plus d'explicite).
- Moyen terme: indicateurs décisionnels par rôle + exports institutionnels normalisés.
- Long terme: plateforme métier ESS multi-structure (paramétrable, connectable, pilotable).

En l'état, ce n'est **pas trop** ambitieux fonctionnellement, mais il faut prioriser la **stabilité d'usage** plutôt que l'ajout de nouveaux modules.
