from app.extensions import db
from app.models import InstanceSettings


def get_or_create_instance_settings() -> InstanceSettings:
    row = InstanceSettings.query.first()
    if row:
        return row
    row = InstanceSettings()
    db.session.add(row)
    db.session.commit()
    return row


def resolve_identity(default_app_name: str, default_org_name: str) -> tuple[str, str, str | None, str | None]:
    row = InstanceSettings.query.first()
    if not row:
        return default_app_name, default_org_name, None, None

    app_name = (row.app_name or "").strip() or default_app_name
    org_name = (row.organization_name or "").strip() or default_org_name
    return app_name, org_name, row.app_logo_path, row.organization_logo_path


def resolve_mail_settings(config: dict) -> dict:
    """Retourne la config SMTP effective (DB prioritaire, fallback env)."""
    row = InstanceSettings.query.first()

    host = (config.get("MAIL_HOST") or "").strip()
    sender = (config.get("MAIL_SENDER") or "").strip()
    username = (config.get("MAIL_USERNAME") or "").strip()
    password = config.get("MAIL_PASSWORD") or ""
    port = int(config.get("MAIL_PORT", 587) or 587)
    use_tls = bool(config.get("MAIL_USE_TLS", True))

    if row:
        host = (row.smtp_host or "").strip() or host
        sender = (row.smtp_sender or "").strip() or sender
        username = (row.smtp_username or "").strip() or username
        password = (row.smtp_password or "") or password
        port = int(row.smtp_port or port)
        use_tls = bool(row.smtp_use_tls if row.smtp_use_tls is not None else use_tls)

    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "use_tls": use_tls,
        "sender": sender,
    }


def resolve_public_base_url(config: dict) -> str:
    """Retourne l'URL publique de l'application (DB prioritaire, fallback env)."""
    row = InstanceSettings.query.first()
    if row and (row.public_base_url or '').strip():
        return row.public_base_url.strip().rstrip('/')
    return (config.get('PUBLIC_BASE_URL') or '').strip().rstrip('/')
