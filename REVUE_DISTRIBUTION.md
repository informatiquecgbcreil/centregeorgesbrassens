# Revue de code – objectif distribution multi-structures

## Résumé exécutif

L’application est déjà proche d’un usage multi-structures grâce à :
- la configuration par variables d’environnement pour la base de données et le serveur,
- un modèle RBAC (rôles/permissions),
- une architecture Flask modulaire (blueprints).

Les principaux freins à la distribution externe sont aujourd’hui :
1. **Branding et identité encore partiellement en dur** (nom d’application / structure),
2. **Sécurité de déploiement** (clé secrète par défaut trop faible pour une diffusion large),
3. **Packaging opérationnel** (données métier potentiellement versionnées, manque de guide d’installation),
4. **Migrations de schéma** pilotées au runtime via `create_all` + SQL ad hoc (efficace en local, plus risqué à grande échelle).

## Points forts constatés

- Priorité aux variables d’environnement pour la DB (`SQLALCHEMY_DATABASE_URI`, `DATABASE_URL`).
- Compatibilité PostgreSQL (`postgres://` normalisé en `postgresql://`).
- Serveur Waitress prêt pour un hébergement Windows/Linux.
- RBAC présent et injecté globalement dans les templates.

## Changements réalisés dans ce commit

### 1) Branding configurable
- Ajout des variables de configuration :
  - `APP_NAME` (défaut: `App Gestion`),
  - `ORGANIZATION_NAME` (défaut: `Votre structure`).
- Injection globale dans les templates via `context_processor`.
- Remplacement de libellés en dur dans les vues principales.

### 2) Impact attendu
- Même code réutilisable pour plusieurs structures sans fork.
- Réduction des traces « Centre Georges Brassens » dans l’interface.

## Recommandations prioritaires (roadmap)

### P0 – Avant diffusion à d’autres structures
1. **Durcir la sécurité de base**
   - Rendre `SECRET_KEY` obligatoire en production (échec explicite au boot si valeur par défaut).
   - Ajouter un guide de variables d’environnement minimales.
2. **Séparer code et données opérationnelles**
   - Exclure explicitement les dépôts de fichiers utilisateurs (`static/uploads/...`) du suivi Git.
   - Prévoir un stockage externe (volume, S3 compatible, etc.) selon contexte.
3. **Formaliser les migrations**
   - Passer d’un schéma auto-modifié au runtime vers des migrations Alembic versionnées.

### P1 – Industrialisation
1. **Ajouter un README de déploiement**
   - Installation, variables requises, création admin, lancement serveur.
2. **Ajouter un fichier `.env.example`**
   - Inclure `APP_NAME`, `ORGANIZATION_NAME`, `SECRET_KEY`, `DATABASE_URL`, etc.
3. **Standardiser les dépendances**
   - Geler les versions manquantes (`Flask-Migrate`, `alembic`) de façon explicite.

### P2 – Gouvernance multi-tenant (si besoin futur)
1. Clarifier si le besoin est **multi-instance** (une DB par structure) ou **multi-tenant dans une même DB**.
2. Si multi-tenant partagé : ajouter un `tenant_id` systématique sur les entités métier critiques + filtrage global.

## Critères d’acceptation suggérés

- [ ] Une structure tierce peut installer l’app avec uniquement `.env` + DB vierge.
- [ ] Aucune mention de structure d’origine n’apparaît en UI par défaut.
- [ ] Les migrations sont reproductibles (dev, staging, prod).
- [ ] Les uploads ne sont plus stockés dans le dépôt Git.
