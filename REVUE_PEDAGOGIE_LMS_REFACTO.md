# Revue de code — Refonte complète du module « pédagogie » vers une logique LMS

## 1) Résumé exécutif

Le module actuel couvre déjà des briques utiles (objectifs, référentiels, compétences, évaluations, présences), mais il reste **orienté saisie locale par session** et pas **pilotage de parcours**.

Conséquences observées dans le code :

- la hiérarchie des objectifs est souple mais peu contrainte (type parent/enfant non sécurisé),
- les liens `projet/atelier/session` et `objectif/compétence` sont nombreux mais non orchestrés,
- l’évaluation est stockée « à plat » (un état par participant/compétence/session) sans moteur de progression,
- les rapports agrègent des volumes d’évaluations mais pas la chaîne causale *compétence -> objectif op -> objectif spécifique -> objectif général*.

Objectif recommandé : passer vers un **modèle LMS orienté trajectoire**, avec propagation d’indicateurs calculés et historisés.

---

## 2) Diagnostic de l’existant (basé sur le code)

### 2.1. Modèle de données : bonnes fondations, mais graphe incomplet

Points positifs :

- `Objectif` gère déjà une hiérarchie (`parent_id`) et plusieurs niveaux (`general/specifique/operationnel`).
- `Competence` est reliée aux `Referentiel`.
- plusieurs associations existent (`projet_competence`, `atelier_competence`, `session_competence`, `objectif_competence`).

Limites bloquantes :

1. **Aucune contrainte métier sur la hiérarchie des objectifs**
   - aujourd’hui, un `Objectif` peut techniquement être lié à n’importe quel parent, sans garantir la logique `général -> spécifique -> opérationnel`.
2. **Rattachements multiples non normalisés**
   - un objectif peut être relié en même temps à projet/atelier/session sans règle explicite selon son type.
3. **Pas de notion de version / validité temporelle**
   - impossible de gérer proprement la rétroactivité (« je modifie un objectif ou un référentiel, que devient l’historique ? »).

---

### 2.2. Flux applicatif : saisie décentralisée et dépendances implicites

- création d’objectifs et liaison de compétences dans une vue unique `/pedagogie/objectifs`.
- évaluation compétence principalement depuis l’émargement de session (`save_evaluation` / `bulk_validate`).
- suivi objectif via un kiosk séparé (`ObjectifSuivi`) avec mode ressenti/compétence/mixte.

Limites :

1. **Deux systèmes de suivi parallèles**
   - `Evaluation` (état compétence) d’un côté,
   - `ObjectifSuivi` (état objectif/ressenti) de l’autre,
   - sans moteur unifié de consolidation.
2. **Agrégation pédagogique faible dans les bilans**
   - les bilans remontent surtout le nombre d’évaluations et leur distribution d’état, mais pas l’atteinte des objectifs par niveau.
3. **Complexité UX forte**
   - beaucoup de sélections manuelles (projet/atelier/session/parent/référentiel/compétences) sans assistant de cohérence.

---

### 2.3. Pourquoi la progression semble « définitive »

Le modèle `Evaluation` garde bien plusieurs lignes (une par session), donc on a un historique brut. Mais l’application n’exploite pas cet historique comme **progression longitudinale** : pas de calcul de tendance, pas de dernier niveau validé avec date, pas de paliers, pas de maîtrise réversible/confirmée.

---

## 3) Cible fonctionnelle (LMS simplifié mais robuste)

### 3.1. Nouvelle chaîne pédagogique explicite

Implémenter une chaîne stricte :

1. **Projet pédagogique**
2. **Objectif général** (1..n par projet)
3. **Objectifs spécifiques** (1..n par objectif général)
4. **Moyens opérationnels** = séquences/ateliers/activités (1..n par objectif spécifique)
5. **Objectifs opérationnels** (1..n par moyen opérationnel)
6. **Compétences évaluables** (1..n par objectif opérationnel)
7. **Évaluations en session** (n événements dans le temps)

Puis calcul ascendant automatique :

- compétence -> score objectif opérationnel,
- objectif opérationnel -> score objectif spécifique,
- objectif spécifique -> score objectif général,
- objectif général -> avancement projet.

---

### 3.2. Principe clé : séparer “faits” et “indicateurs calculés”

- **Faits** (immutables/historisés) : présences, évaluations, changements de structure.
- **Indicateurs** (recalculables) : taux d’atteinte, niveaux consolidés, tendance, retard/alerte.

Cela permet la rétroactivité : si vous changez un mapping objectif-compétence, vous relancez un recalcul à période donnée.

---

## 4) Refonte technique recommandée

## 4.1. Données : schéma cible minimal

Ajouter (ou faire évoluer vers) :

1. `learning_path` (par projet ou cohorte)  
2. `learning_goal` (fusion/évolution de `Objectif` avec contraintes de type et parentage validé)  
3. `learning_unit` (atelier/séquence pédagogique planifiée)  
4. `goal_competency_map` (pondération, seuil, validité temporelle `valid_from/valid_to`)  
5. `competency_assessment_event` (événements d’évaluation : niveau, évaluateur, contexte)  
6. `participant_competency_state` (état consolidé calculé : dernier niveau, progression, confiance)  
7. `goal_progress_snapshot` (photo calculée par date/période pour reporting).

Important : garder `Evaluation` au début comme source legacy, puis migrer vers des événements explicites.

---

### 4.2. Règles métier à imposer

- parentage autorisé :
  - général -> spécifique,
  - spécifique -> opérationnel,
  - opérationnel -> (pas d’enfant, mais mapping compétences).
- rattachement attendu selon le type :
  - général : projet obligatoire,
  - spécifique : moyen opérationnel/atelier obligatoire,
  - opérationnel : unité/séance obligatoire.
- pondération totale des compétences d’un objectif opérationnel = 100% (ou normalisation automatique).
- toute modification structurelle crée une nouvelle version logique (validité temporelle).

---

### 4.3. Moteur de progression

Au lieu d’un « état final » :

- calculer pour chaque participant et compétence :
  - dernier niveau,
  - meilleur niveau,
  - tendance (3 dernières évaluations),
  - niveau stabilisé (ex: confirmé sur 2 séances).
- score objectif opérationnel = somme pondérée des compétences liées.
- score objectif spécifique/général = agrégation pondérée des enfants.

Résultat attendu : parcours visible, évolutif, jamais “figé”.

---

### 4.4. Reporting activité / financeurs

Prévoir une vue d’export orientée rapport :

- par projet : avancement objectif général + détails spécifiques/opérationnels,
- par période : progression des cohortes/participants,
- par référentiel : couverture et niveau moyen,
- indicateurs qualité : % compétences non évaluées, délais moyens d’acquisition, taux de régression.

Exporter CSV/XLSX et alimenter le rapport d’activité directement.

---

## 5) Plan de migration pragmatique (sans big bang)

### Phase 0 — Quick wins (2–3 semaines)

- Ajouter garde-fous de cohérence sur création d’objectifs (type/parent/rattachements).
- Ajouter écran de lecture “chaîne pédagogique” (vue graphe/arbre), sans modifier la saisie.
- Ajouter indicateur de progression participant/compétence basé sur l’historique `Evaluation` existant.

### Phase 1 — Modèle versionné (3–5 semaines)

- Introduire tables de mapping versionné objectif-compétence.
- Introduire snapshots de progression recalculables.
- Job de recalcul rétroactif (par projet/période).

### Phase 2 — Unification du suivi (3–4 semaines)

- Fusionner logique `ObjectifSuivi` et `Evaluation` dans un pipeline unique (événements + consolidé).
- Conserver compatibilité UI, puis basculer progressivement les écrans.

### Phase 3 — Reporting LMS (2–4 semaines)

- Nouveau module “Rapport d’activité pédagogique” avec exports.
- API interne pour bilans/financeurs.

---

## 6) Priorités produit (ordre recommandé)

1. **Cohérence du modèle** (types, parentage, version)  
2. **Progression des participants** (historique exploité, non définitif)  
3. **Propagation automatique vers objectifs**  
4. **Reporting exploitable financeurs/activité**  
5. **Simplification UX** (assistants, préremplissage, moins de champs manuels).

---

## 7) Conclusion

Votre besoin est cohérent avec une architecture LMS “light” : ce n’est pas un problème d’écran uniquement, c’est surtout un problème de **modèle et de moteur de calcul**. La base actuelle permet une migration incrémentale sans tout casser, mais il faut introduire rapidement :

- des contraintes de structure,
- une logique versionnée,
- un calcul de progression longitudinal,
- un reporting orienté chaîne d’objectifs.

C’est ce qui permettra enfin l’**interdépendance (y compris rétroactive)** entre objectifs, référentiels, sessions et évaluations.
