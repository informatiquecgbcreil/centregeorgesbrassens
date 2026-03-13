# Application de gestion financière et statistique d'un centre social

Application web de gestion d’activités, d’ateliers et de suivi pédagogique,
conçue pour des structures associatives, sociales et éducatives.

## Objectif du projet

Cette application vise à :
- structurer le suivi des activités et ateliers ;
- faciliter la définition et l’évaluation des objectifs pédagogiques ;
- produire des bilans exploitables sans surcharge administrative ;
- réduire la dépendance aux fichiers Excel dispersés.

L’application est pensée pour un usage **terrain**, par des salarié·e·s non techniques,
avec une attention particulière portée à l’UX et à la charge mentale.

## Origine du projet

Ce projet est développé **à titre personnel**, en dehors de tout cadre contractuel professionnel.

Il a été conçu à partir d’un besoin métier réel, observé dans le secteur associatif,
et développé sur temps personnel.

Le développement a été réalisé avec l’assistance d’outils d’intelligence artificielle
(ChatGPT, Codex), utilisés comme aides à la production.
Les choix fonctionnels, architecturaux et pédagogiques relèvent du mainteneur du projet.

## Licence

Ce projet est distribué sous licence **GNU Affero General Public License v3 (AGPL v3)**.

Cela signifie notamment que :
- le code est librement utilisable, modifiable et redistribuable ;
- toute modification utilisée via un service réseau doit être rendue publique sous la même licence ;
- aucune appropriation propriétaire du code n’est autorisée.

Une **licence commerciale** pourra être proposée ultérieurement pour des usages spécifiques
(institutionnels, intégration propriétaire, déploiements contraints).

## Public cible

- Centres sociaux
- Associations socio-éducatives
- Structures d’éducation populaire
- Acteurs de l’ESS

## Statut du projet

Le projet est en cours de stabilisation fonctionnelle.
Il n’est pas encore présenté comme un produit clé-en-main,
mais comme une base solide et évolutive.

## Contributions

Les contributions externes ne sont pas ouvertes par défaut à ce stade.
Toute proposition devra faire l’objet d’un échange préalable avec le mainteneur.

---

Mainteneur : Antoine Vivien

## Vérifications rapides de fiabilité

Pour exécuter un socle de contrôles (boot app, RBAC, endpoint de santé) :

```bash
python tools/run_reliability_checks.py
```

Pour inclure la vérification des comptes de test (`admin.test@mars.local`, `directeur.test@mars.local`) :

```bash
python tools/run_reliability_checks.py --require-test-users
```

Pour inclure aussi le test des routes de récupération de mot de passe :

```bash
python tools/run_reliability_checks.py --require-test-users --check-password-reset
```


## Récupération de mot de passe

Les utilisateurs peuvent désormais demander un lien de réinitialisation depuis l'écran de connexion via **Mot de passe oublié ?**.


Pendant la première installation (`/setup`), vous pouvez aussi renseigner le SMTP (hôte, port, expéditeur, TLS) pour activer immédiatement les emails de récupération.

- Route de demande: `/password-reset`
- Route de réinitialisation: `/password-reset/<token>`
- Le lien expire automatiquement (configurable via `PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS`).
- L'envoi SMTP est borné dans le temps pour éviter les pages bloquées (`MAIL_TIMEOUT_SECONDS`, défaut 10s).
- Compat SMTP: le port **465** est traité en SSL implicite (SMTPS), le port **587** en STARTTLS.
- Pour les utilisateurs sur d'autres postes, définissez une **URL publique** (dans le setup/admin) ou `ERP_PUBLIC_BASE_URL` (ex: `http://192.168.1.10:8000`) afin de générer des liens email accessibles hors machine hôte.

