# Patch notes — dashboard age unknown drilldown — 2026-03-13

## Correctif
Le drill-down de la pyramide / répartition par âge ne permettait pas d'ouvrir la liste des participants pour les âges non renseignés, car cette catégorie n'était pas représentée comme une vraie entrée du graphique.

## Modifications
- ajout d'une barre **« Âge non renseigné »** dans le graphique des âges du dashboard ;
- ajout de l'URL de drill-down correspondante ;
- correction du filtre participants pour interpréter correctement `age_bucket=Âge non renseigné` ;
- évitement du doublon dans la zone méta du graphique.

## Fichiers touchés
- `app/services/dashboard_service.py`
- `app/participants/routes.py`
- `app/templates/dashboard.html`

## Effet attendu
- clic sur **Âge non renseigné** dans le dashboard → ouverture de la page participants filtrée sur les personnes sans âge exploitable pour la période ;
- plus de catégorie orpheline sans renvoi utilisable.
