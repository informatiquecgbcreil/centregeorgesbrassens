# Refonte MVP module pédagogie (pilotage & impact)

## 1) Inventaire précis (modèles + routes)

### Modèles SQLAlchemy utilisés
- `Referentiel`, `Competence`, `Objectif`, `ObjectifSuivi`, `Evaluation` (noyau pédagogie historique).
- `Projet`, `ProjetAtelier` (liaison projet-atelier), `AtelierActivite`, `SessionActivite`.
- `Participant`, `PresenceActivite`.
- **Nouveaux** : `PedagogieModule`, `ObjectifCompetenceMap` + tables `module_competence`, `atelier_module`, `session_module`.

### Flux actuel confirmé dans le code
- Référentiels/compétences : `app/pedagogie/routes.py` (`/pedagogie/referentiels`, `/pedagogie/referentiels/<id>`).
- Objectifs : `/pedagogie/objectifs`.
- Création sessions + évaluations : `app/activite/routes.py` (`/atelier/<id>/session/new`, `/session/<id>/emargement`).
- Bilans : `app/bilans/services.py` agrège surtout volume d’évaluations (`total`, `par_etat`, `nb_competences_uniques`).

### Historique d’évaluation : événementiel ou écrasé ?
- Le modèle `Evaluation` est **événementiel par session** grâce à la contrainte unique `(participant_id, competence_id, session_id)`.
- Donc on peut avoir plusieurs évaluations dans le temps pour une même compétence (sessions différentes).
- Limite ancienne : l’app exploitait peu cette timeline.

## 2) Architecture MVP (light)

### Brique A — Interdépendance + rétroactivité
- Ajout de `ObjectifCompetenceMap` : mapping explicite `objectif opérationnel <-> compétence` avec `poids` et `actif`.
- Les scores objectifs/projets sont calculés à la volée depuis les évaluations (pas de figement), donc rétroactifs.

### Brique B — Progression participants
- Réutilisation de `Evaluation` comme événements datés.
- Ajout d’un passeport participant (`/pedagogie/participant/<id>/passeport`) : niveau actuel = dernière évaluation, timeline complète.
- Niveau simplifié utilisé en UI : `0 non abordé`, `1 vu/compris`, `2 fait avec aide`, `3 fait seul`.

### Brique C — Réduction de saisie
- Ajout de modules pédagogiques réutilisables (`PedagogieModule`).
- En création de session : sélection d’un ou plusieurs modules -> pré-cochage des compétences; sélection manuelle conservée.

## 3) Schéma DB + migration

### Nouvelles structures
- `pedagogie_module`
- `module_competence`
- `atelier_module`
- `session_module`
- `objectif_competence_map` (poids + actif)

### Migration
- Fichier Alembic : `migrations/versions/b1c2d3e4f5a6_pedagogie_mvp_light.py`.
- Bootstrap automatique : copie des liens existants `objectif_competence` vers `objectif_competence_map` avec poids=1.

## 4) Moteur de calcul rétroactif

- Service `app/pedagogie/services.py`:
  - `compute_objectif_scores(...)` calcule score OO puis agrégation vers OS/OG par arbre parent/enfant.
  - `participant_timeline(...)` renvoie timeline + niveaux courants.
- Le calcul utilise les données d’évaluation historiques filtrables par période (`start_date`, `end_date`).

## 5) UI minimale livrée

- Création session (`app/templates/activite/session_form.html`):
  - choix modules,
  - pré-sélection auto des compétences,
  - sélection manuelle toujours possible.
- Évaluation (`app/templates/activite/emargement.html`):
  - labels niveaux simplifiés 0..3,
  - accès direct au passeport participant.
- Pilotage objectifs (`/pedagogie/pilotage`): vue tabulaire calculée OG/OS/OO.
- Modules (`/pedagogie/modules`): création simple des packs.

## 6) Export rapport d’activité

- Route: `/pedagogie/export_ra.csv`
- Colonnes : projet, type objectif, objectif, score atteinte, nb évaluations, nb participants.
- Filtrable par projet + période via query params.

## 7) TODO courte (itération suivante)

1. Édition des poids/activation directement dans l’UI objectif.
2. Agrégation pondérée configurable OO->OS->OG->Projet.
3. Affichage des noms de compétences dans le passeport (actuellement déjà disponible sur timeline; enrichir la synthèse niveau actuel).
4. Ajouter test d’intégration Flask pour export CSV et recalcul rétroactif.
5. Ajouter snapshot optionnel pour gros volumes (pré-calcul nocturne).

## 8) Scénarios d’acceptation (10)

1. Créer un OO + lier 2 compétences avec poids 1/1 -> score OO calculé.
2. Modifier après coup le mapping OO<->compétence -> score pilotage change sans modifier les évaluations passées.
3. Désactiver un mapping (`actif=false`) -> contribution retirée du score.
4. Évaluer 3 fois la même compétence (sessions différentes) -> timeline affiche 3 événements.
5. Niveau actuel participant = dernière évaluation chronologique.
6. Créer un module avec 4 compétences -> session avec module pré-coche ces 4 compétences.
7. Ajouter manuellement une 5e compétence en session -> persistée aussi.
8. Pilotage projet filtré par période réduit les évaluations prises en compte.
9. Export CSV période N contient les mêmes chiffres que la vue pilotage filtrée N.
10. Créer OS/OG sans mapping direct -> score hérité par agrégation des enfants OO.
