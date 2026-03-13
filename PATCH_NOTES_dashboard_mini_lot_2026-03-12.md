# Patch notes — mini-lot dashboard UX + graphiques publics

## Base
Patch construit à partir de `app_gestion_v1.2.14_patch_lot3_ux_chirurgical_2026-03-12.zip` avec intégration du lot dashboard.

## Contenu du patch

### 1) Filtres de période du dashboard
- ajout d’un filtre de période explicite : **30 jours / 90 jours / 365 jours / Année** ;
- conservation de l’exercice budgétaire en parallèle ;
- lorsque **Année** est sélectionné, la période d’activité suit l’exercice choisi dans le champ **Exercice**.

### 2) Nouveaux graphiques interactifs sur le dashboard
Ajout de trois graphiques réactifs aux filtres de période :
- **Répartition femmes / hommes** ;
- **Répartition par âge** (tranches d’âge) ;
- **Villes et quartiers** sous forme de **camembert imbriqué** : villes au centre, quartiers à l’extérieur.

### 3) Interactivité
- survol des segments/barres avec info-bulle ;
- légendes cliquables ;
- mise en évidence visuelle d’une catégorie ;
- redessin propre au redimensionnement de la fenêtre.

### 4) Nettoyage UX du dashboard
- suppression de plusieurs formulations trop relâchées ;
- harmonisation en **vouvoiement** ;
- sous-titres et aides plus professionnels ;
- intitulés de filtres et d’actions plus explicites.

## Fichiers modifiés
- `app/main/routes.py`
- `app/services/dashboard_service.py`
- `app/templates/dashboard.html`

## Vérifications effectuées
- compilation Python : **OK**
- analyse syntaxique Jinja : **OK**

## Limites connues
- le graphique **Villes et quartiers** dépend directement de la qualité des données `ville` / `quartier` renseignées sur les participants ;
- la répartition femmes / hommes repose sur le champ `genre` et regroupe les valeurs non standard dans **Autre** ou **Non renseigné** ;
- la répartition par âge dépend de la présence d’une `date_naissance` exploitable.
