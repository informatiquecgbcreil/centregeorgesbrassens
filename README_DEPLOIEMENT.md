# Déploiement – App Gestion (multi-structures)

Ce guide permet d’installer l’application dans une nouvelle structure sans modifier le code.

## 1) Prérequis
- Python 3.11+
- Une base PostgreSQL (recommandé en prod) ou SQLite (petites instances)
- Variables d’environnement configurées (voir `.env.example`)

## 2) Installation
```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## 3) Configuration
1. Copier `.env.example` vers `.env`.
2. Adapter au contexte de la structure:
   - `APP_NAME`
   - `ORGANIZATION_NAME`
   - `SECRET_KEY` (forte, unique)
   - `DATABASE_URL` (PostgreSQL recommandé)
   - `ERP_PUBLIC_BASE_URL`

## 4) Démarrage
Option simple:
- Windows: `start_windows.bat`
- Linux: `./start_linux.sh`

Option manuelle:
```bash
python run_waitress.py
```

Par défaut, l’application écoute sur `127.0.0.1:8000`.

Au premier lancement, l’application redirige automatiquement vers `/setup/` pour créer l’administrateur initial.

## 5) Recommandations production
- Positionner `ERP_ENV=production`.
- Ne jamais conserver la `SECRET_KEY` par défaut.
- Exécuter derrière un reverse-proxy (Nginx/Caddy/IIS) en HTTPS.
- Sauvegarder la base de données et le répertoire d’uploads régulièrement.

## 6) Stratégie de distribution recommandée
- **Multi-instance** uniquement: **1 base par association** (aucune mutualisation des données).
- 1 configuration `.env` et 1 dossier d’uploads par instance.

## 7) Personnalisation sans code
Une fois connecté avec un compte admin technique:
- ouvrir **Administration > Identité app**
- saisir/modifier: nom de l’application, nom de la structure, logo application, logo structure.
Ces éléments sont modifiables à tout moment depuis l’interface.


## 8) Sauvegarde / restauration
- Sauvegarde: `python tools/backup_instance.py`
- Restauration: `python tools/restore_instance.py --db <fichier.db|fichier.sql> --uploads <archive_uploads.zip>`
- Sous Windows, des raccourcis sont fournis: `backup_now.bat` et `restore_now.bat`.
- Voir aussi `RUNBOOK_PROD.md` pour la checklist d’exploitation.


## 9) Migrations DB (industrialisées)
- L'application utilise Alembic/Flask-Migrate (dossier `migrations/`).
- Au démarrage, `db upgrade` est appliqué automatiquement (`DB_AUTO_UPGRADE_ON_START=1`).
- Exécution manuelle possible:
```bash
flask --app wsgi:app db upgrade
```
- Le mode legacy (`DB_ENABLE_LEGACY_SCHEMA_PATCH`) doit rester désactivé sauf reprise d'anciennes bases.
- Si une base historique existe déjà sans table `alembic_version`, l'app applique automatiquement un `stamp(head)` puis `upgrade` (évite l'erreur "table ... already exists").


## 10) Kit hébergement standard
- Linux: templates `systemd`, `nginx`, `cron` dans `deploy/linux/`.
- Windows: scripts NSSM + tâche planifiée dans `deploy/windows/`.
- Guide pas-à-pas: `KIT_HEBERGEMENT_STANDARD.md`.
