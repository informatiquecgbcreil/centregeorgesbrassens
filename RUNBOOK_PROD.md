# Runbook production (très concret)

## Avant mise en service
- [ ] `ERP_ENV=production`
- [ ] `SECRET_KEY` personnalisée
- [ ] PostgreSQL recommandé (ou SQLite pour petite structure)
- [ ] Sauvegarde testée
- [ ] HTTPS en frontal (Nginx/Caddy/IIS)

## Sauvegarder maintenant
### Windows
- Double-cliquer `backup_now.bat`

### Linux/Web
```bash
python tools/backup_instance.py
```

Résultat dans le dossier `backups/`:
- fichier DB (`.db` ou `.sql`)
- archive uploads (`*_uploads.zip`)

## Restaurer
### Windows
- Double-cliquer `restore_now.bat` puis indiquer les 2 fichiers

### Linux/Web
```bash
python tools/restore_instance.py --db <fichier.db|fichier.sql> --uploads <archive_uploads.zip>
```

## Contrôles après restauration
- [ ] Connexion admin OK
- [ ] Logos visibles
- [ ] Pièces jointes (factures/projets) accessibles
- [ ] Tableau de bord charge sans erreur


## Migration DB
- Vérifier que `DB_AUTO_UPGRADE_ON_START=1`.
- En maintenance contrôlée, vous pouvez lancer manuellement:
```bash
flask --app wsgi:app db upgrade
```


## Hébergement standardisé
- Linux: appliquer `deploy/linux/systemd/app-gestion.service` + `deploy/linux/nginx/app-gestion.conf`.
- Windows: appliquer `deploy/windows/install_nssm_service.ps1`.
- Voir `KIT_HEBERGEMENT_STANDARD.md`.
