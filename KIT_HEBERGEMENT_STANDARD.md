# Kit d'hébergement standard (Linux + Windows)

Ce kit permet de déployer l'application sans support développeur.

## 1) Linux (recommandé)

### Fichiers fournis
- `deploy/linux/systemd/app-gestion.service`
- `deploy/linux/nginx/app-gestion.conf`
- `deploy/linux/cron/backup.cron`

### Étapes
1. Copier l'application dans `/opt/app-gestion`.
2. Copier `.env.example` en `.env` et renseigner les variables.
3. Créer l'utilisateur système `appgestion`.
4. Installer le service systemd:
   ```bash
   sudo cp deploy/linux/systemd/app-gestion.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now app-gestion
   ```
5. Installer la conf Nginx et activer HTTPS (certbot ou reverse-proxy existant).
6. Activer la sauvegarde quotidienne:
   ```bash
   sudo crontab -u appgestion deploy/linux/cron/backup.cron
   ```

## 2) Windows

### Fichiers fournis
- `deploy/windows/install_nssm_service.ps1`
- `deploy/windows/register_backup_task.ps1`

### Étapes
1. Installer l'application dans `C:\AppGestion`.
2. Ouvrir PowerShell en administrateur.
3. Installer le service Windows via NSSM:
   ```powershell
   .\deploy\windows\install_nssm_service.ps1 -AppDir "C:\AppGestion" -NssmPath "C:\tools\nssm\win64\nssm.exe"
   ```
4. Planifier la sauvegarde quotidienne:
   ```powershell
   .\deploy\windows\register_backup_task.ps1 -AppDir "C:\AppGestion" -At "02:30"
   ```

## 3) Contrôles d'exploitation
- Vérifier `http://127.0.0.1:8000/healthz` -> `{"status":"ok"}`.
- Vérifier qu'un backup est généré dans `backups/`.
- Vérifier le redémarrage automatique du service.

## 4) Migration d'une base historique
- Conserver `DB_AUTO_UPGRADE_ON_START=1`.
- L'application gère automatiquement le cas "base déjà créée sans alembic_version".
