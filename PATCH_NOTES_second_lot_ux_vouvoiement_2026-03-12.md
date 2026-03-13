# Patch notes — second lot UX / écrans bavards ou datés

Base utilisée : `app_gestion_v1.2.14_patch_fix_graphs_resize_2026-03-11.zip`

## Contenu du lot

Ce lot poursuit le nettoyage UX et rédactionnel sur les écrans encore trop bavards, trop internes ou trop peu explicites.

### Écrans retravaillés
- Émargement
- Tableau de bord `statsimpact`
- Exports `statsimpact`
- Synthèse `stats_bilans`
- Bilans : dashboard, secteur, subvention, financeurs, inventaire, qualité, bilans détaillés
- Vues admin : utilisateurs, instance, attribution des rôles, secteurs, droits

### Améliorations apportées
- Harmonisation du **vouvoiement** sur les écrans ciblés
- Suppression de nombreuses tournures trop familières, floues ou internes
- Ajout d’un petit socle commun d’aide contextuelle :
  - macro Jinja `_ux_helpers.html`
  - encarts `À savoir` / `Lecture` / `Interprétation`
  - icônes d’aide simples via `title`
- Clarification de plusieurs libellés de boutons, titres et sous-titres
- Nettoyage des messages flash côté `admin/routes.py`

### Fichiers modifiés
- `app/templates/layout.html`
- `app/templates/_ux_helpers.html`
- `app/templates/activite/emargement.html`
- `app/templates/statsimpact/dashboard.html`
- `app/templates/statsimpact/_dashboard_body.html`
- `app/templates/statsimpact/exports.html`
- `app/templates/stats_bilans.html`
- `app/templates/admin_users.html`
- `app/templates/admin_instance.html`
- `app/templates/admin_rbac_users.html`
- `app/templates/admin_secteurs.html`
- `app/templates/admin_droits.html`
- `app/templates/admin_rbac_role_edit.html`
- `app/templates/bilans_dashboard.html`
- `app/templates/bilans_secteur.html`
- `app/templates/bilans_subvention.html`
- `app/templates/bilans_financeurs.html`
- `app/templates/bilans_inventaire.html`
- `app/templates/bilans_qualite.html`
- `app/templates/bilans_lourds.html`
- `app/admin/routes.py`

## Vérifications effectuées
- Compilation Python : OK
- Chargement Jinja des templates : OK

## Remarque
Ce lot améliore nettement les écrans les plus exposés, mais il ne remplace pas encore une relecture intégrale de tous les écrans secondaires. Il reste encore quelques textes à harmoniser dans d’autres vues d’activité et de formulaires annexes.
