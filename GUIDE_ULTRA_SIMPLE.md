# Installation ultra-simple (Windows / Linux / Web)

Objectif: permettre à une association d’installer l’application sans connaissances techniques avancées.

## Ce qu’il faut retenir
- Une association = une base de données séparée.
- Pas de partage de données entre associations.
- Après installation, les noms/logos se règlent directement dans l’écran **Administration > Identité app**.

## A) Installation la plus simple (ordinateur Windows ou Linux)
1. Ouvrir le dossier de l’application.
2. Lancer le script adapté:
   - Windows: double-clic sur `start_windows.bat`
   - Linux: `./start_linux.sh`
3. Ouvrir le navigateur sur `http://127.0.0.1:8000/setup/`.
4. Suivre l’assistant (nom app, nom structure, admin, mot de passe).
5. Se connecter puis aller dans **Administration > Identité app** pour les logos.

## B) Hébergement Web (si l’association le souhaite)
- Même application, même principe, mais hébergée sur un serveur.
- Recommandé: PostgreSQL + HTTPS + sauvegardes régulières.
- Lancer toujours avec `ERP_ENV=production`.

## C) Personnaliser sans code
- Connectez-vous avec le compte admin technique.
- Menu **Administration > Identité app**.
- Renseignez:
  - Nom de l’application
  - Nom de la structure
  - Logo application
  - Logo structure
- Cliquez **Enregistrer la configuration**.

## D) En cas de problème
- Vérifier que `SECRET_KEY` est bien renseignée.
- Vérifier que le port `8000` n’est pas déjà utilisé.
- Vérifier que la base de données est accessible.


IMPORTANT : la toute première ouverture redirige automatiquement vers l’assistant de démarrage.


## E) Sauvegarder vos données
- Windows: double-cliquer `backup_now.bat`.
- Linux/Web: lancer `python tools/backup_instance.py`.
- Les fichiers de sauvegarde sont déposés dans le dossier `backups/`.


Bon à savoir: les mises à jour de base de données sont appliquées automatiquement au démarrage.


## F) Hébergement
Si la structure veut héberger sur serveur, utiliser directement le guide `KIT_HEBERGEMENT_STANDARD.md`.
