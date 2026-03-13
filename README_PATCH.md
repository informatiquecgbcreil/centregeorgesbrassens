# Patch Option A — Pédagogie

## Objectifs
1) **Rattacher / modifier les modules d'une session a posteriori**
   - Nouvelle route : `/session/<id>/modules`
   - Mise à jour `session.modules`
   - Recalcule automatiquement `session.competences` = union des compétences des modules sélectionnés
   - Bouton ajouté dans l'émargement : **Modules (a posteriori)**

2) **Évaluer un participant hors session (ou rattacher une évaluation à une session existante)**
   - Nouvelle route : `POST /pedagogie/participant/<id>/passeport/evaluation`
   - Ajout d'un formulaire "Évaluation rapide" dans le passeport participant

## Installation
Copier les fichiers du patch dans ton projet en conservant l'arborescence :

- `app/activite/routes.py`
- `app/pedagogie/routes.py`
- `app/templates/activite/emargement.html`
- `app/templates/activite/session_modules.html` (nouveau)
- `app/templates/pedagogie/participant_passeport.html`

Puis redémarrer l'application.

## Notes
- Ce patch **n'ajoute pas de migration** : il utilise la table `session_module` déjà présente dans `models.py`.
- L'évaluation hors session utilise `Evaluation.session_id = NULL`.
