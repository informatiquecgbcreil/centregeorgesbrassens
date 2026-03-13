# Patch UX / vouvoiement — 2026-03-11

## Contenu du patch
- ajout d'un kit UX commun pour les info-bulles et encarts d'aide (`app/templates/_ux_macros.html`)
- ajout des styles associés dans `app/static/style.css`
- réécriture en vouvoiement de nombreux libellés, aides et messages visibles
- nettoyage de formulations trop familières dans plusieurs routes Flask
- refonte ciblée de formulaires prioritaires : setup, utilisateurs, atelier, session, participant, passeport, synthèse d'activité, exports

## Fichiers principalement retravaillés
- `app/templates/setup/wizard.html`
- `app/templates/admin_users.html`
- `app/templates/activite/atelier_form.html`
- `app/templates/activite/session_form.html`
- `app/templates/participants/form.html`
- `app/templates/pedagogie/_passeport_eval_form.html`
- `app/templates/stats_bilans.html`
- `app/templates/statsimpact/exports.html`
- plusieurs autres templates et routes pour harmonisation textuelle

## Vérifications effectuées
- compilation Python (`compileall`) : OK
- analyse syntaxique Jinja sur les templates : OK

## Limite actuelle
Ce patch ne réécrit pas encore l'intégralité de tous les écrans secondaires de l'application. Il pose cependant un socle commun réutilisable et nettoie déjà une partie importante des écrans les plus exposés.
