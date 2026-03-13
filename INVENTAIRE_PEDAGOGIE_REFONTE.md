# Inventaire précis + wireframe + schéma MVP (pilotage & impact)

## A) Inventaire précis

## 1) Modèles / tables existants pertinents

### Noyau pédagogie
- `Referentiel` (`referentiel`)
- `Competence` (`competence`)
- `Objectif` (`objectif`) : hiérarchie via `parent_id`, types `general/specifique/operationnel`
- `Evaluation` (`evaluation`) : événements datés par participant/compétence/session
- `ObjectifSuivi` (`objectif_suivi`) : suivi complémentaire mode ressenti/compétence

### Activité terrain
- `AtelierActivite` (`atelier_activite`)
- `SessionActivite` (`session_activite`)
- `Participant` (`participant`)
- `PresenceActivite` (`presence_activite`)

### Projet
- `Projet` (`projet`)
- `ProjetAtelier` (`projet_atelier`) : association projet ↔ atelier

### Tables d’association historiques
- `atelier_competence`
- `session_competence`
- `objectif_competence`
- `projet_competence`

### Ajouts MVP (dans cette PR)
- `PedagogieModule` (`pedagogie_module`)
- `module_competence`
- `atelier_module`
- `session_module`
- `ObjectifCompetenceMap` (`objectif_competence_map`) : mapping OO↔compétence pondéré/actif
- `PlanProjetAtelierModule` (`plan_projet_atelier_module`) : plan projet → atelier → modules autorisés
- `objectif.module_id` : OO lié à un module

## 2) Routes et flux

### Routes pédagogie
- `/pedagogie/referentiels` (+ édition)
- `/pedagogie/modules` (catalogue modules)
- `/pedagogie/objectifs` (assistant OG/OS/OO)
- `/pedagogie/plan_projet` (plan projet→atelier→modules)
- `/pedagogie/pilotage` (scores rétroactifs)
- `/pedagogie/export_ra.csv` (export rapport d’activité)
- `/pedagogie/participant/<id>/passeport` (timeline + niveau actuel)

### Routes terrain activité
- `/activite/atelier/<id>/session/new` (création session avec modules proposés)
- `/activite/session/<id>/emargement` (émargement + accès évaluation)
- `/activite/session/<id>/evaluation_batch` (grille batch 0/1/2/3)

## 3) Templates impactés
- `app/templates/pedagogie/objectifs.html`
- `app/templates/pedagogie/modules.html`
- `app/templates/pedagogie/plan_projet.html`
- `app/templates/pedagogie/pilotage.html`
- `app/templates/pedagogie/participant_passeport.html`
- `app/templates/activite/session_form.html`
- `app/templates/activite/emargement.html`
- `app/templates/activite/evaluation_batch.html`

## 4) Évaluations: événementiel ou écrasement ?
- Le stockage est **événementiel** (multi-évaluations dans le temps) : contrainte unique `(participant_id, competence_id, session_id)`.
- Donc une même compétence peut être réévaluée à chaque session sans écraser les sessions précédentes.

---

## B) Wireframe texte des 3 écrans cibles

## 1) CATALOGUE (admin)
- Onglet 1 : Référentiels
- Onglet 2 : Compétences
- Onglet 3 : Modules
  - Nom module
  - Description
  - Compétences du module

## 2) PLAN DE PROJET (chargé de projet)
- Sélecteur Projet
- Tableau “Atelier -> Modules autorisés”
- Bouton “Ajouter lien”
- Liste des liens actifs avec suppression

## 3) TERRAIN / SESSION (animateur)
- Étape 1 : Créer session (atelier/date)
- Étape 2 : Choisir module(s) proposé(s) automatiquement selon plan
- Étape 3 : Évaluer en grille batch participants × compétences, boutons 0/1/2/3, sauvegarde unique

---

## C) Schéma MVP minimal + plan de migration

## Tables/colonnes ajoutées
1. `pedagogie_module`
2. `module_competence`
3. `atelier_module`
4. `session_module`
5. `objectif_competence_map`
6. `plan_projet_atelier_module`
7. `objectif.module_id`

## Migrations
- `b1c2d3e4f5a6_pedagogie_mvp_light.py` : modules + mapping OO↔compétence (avec bootstrap depuis `objectif_competence`).
- `c3d4e5f6a7b8_plan_projet_module_and_objectif_module.py` : plan projet + FK module sur `objectif`.

## Compatibilité
- Historique `evaluation` conservé.
- Calculs rétroactifs reposent sur faits (`evaluation`) + mappings (`objectif_competence_map`, `plan_projet_atelier_module`).

---

## D) TODO court
1. Sélecteur parent intelligent OG→OS→OO (auto-parenting).
2. Édition poids/actif des mappings OO↔compétences dans UI dédiée.
3. Droits RBAC fins (catalogue vs terrain).
4. Export XLSX en plus du CSV.
