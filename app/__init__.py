import os

from flask import Flask, url_for, abort, request, redirect
from werkzeug.routing import BuildError

from sqlalchemy import text, inspect
from sqlalchemy.exc import OperationalError, ProgrammingError

from config import Config, DEFAULT_SECRET_KEY
from app.extensions import db, login_manager, csrf, migrate
from app.models import User


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)

    # Instance folder (sqlite db, uploads, etc.)
    os.makedirs(app.instance_path, exist_ok=True)

    default_secret = app.config.get("SECRET_KEY") == DEFAULT_SECRET_KEY
    is_prod_env = app.config.get("ERP_ENV") == "production"

    if default_secret and is_prod_env:
        raise RuntimeError(
            "SECRET_KEY par défaut interdite en production. Définis SECRET_KEY via variable d'environnement."
        )

    if default_secret and not app.debug:
        app.logger.warning(
            "SECRET_KEY par défaut détectée. Définis SECRET_KEY via variable d'environnement pour la prod."
        )

    # Extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    login_manager.login_view = "auth.login"

    # ------------------------------------------------------------------
    # Jinja helper: safe_url_for
    # ------------------------------------------------------------------
    def safe_url_for(endpoint: str, fallback: str = "#", **values) -> str:
        try:
            return url_for(endpoint, **values)
        except BuildError:
            return fallback

    app.jinja_env.globals["safe_url_for"] = safe_url_for

    from app.services.storage import send_media_file, media_url

    @app.route("/media/<path:filename>")
    def media_file(filename):
        return send_media_file(filename, as_attachment=False)

    @app.route("/healthz")
    def healthz():
        return {"status": "ok"}, 200

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # ------------------------------------------------------------------
    # Blueprints
    # ------------------------------------------------------------------
    from app.auth.routes import bp as auth_bp
    from app.main.routes import bp as main_bp
    from app.budget.routes import bp as budget_bp
    from app.projets.routes import bp as projets_bp
    from app.admin.routes import bp as admin_bp
    from app.activite import bp as activite_bp
    from app.kiosk import bp as kiosk_bp
    from app.statsimpact.routes import bp as statsimpact_bp
    from app.bilans.routes import bp as bilans_bp
    from app.inventaire.routes import bp as inventaire_bp
    from app.inventaire_materiel.routes import bp as inventaire_materiel_bp
    from app.participants.routes import bp as participants_bp
    from app.launcher import bp as launcher_bp
    from app.pedagogie.routes import bp as pedagogie_bp
    from app.quartiers import bp as quartiers_bp
    from app.partenaires import bp as partenaires_bp
    from app.questionnaires import bp as questionnaires_bp
    from app.setup import bp as setup_bp

    app.register_blueprint(setup_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(budget_bp)
    app.register_blueprint(projets_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(activite_bp)
    app.register_blueprint(kiosk_bp)
    app.register_blueprint(statsimpact_bp)
    app.register_blueprint(bilans_bp)
    app.register_blueprint(inventaire_bp)
    app.register_blueprint(inventaire_materiel_bp)
    app.register_blueprint(participants_bp)
    app.register_blueprint(launcher_bp)
    app.register_blueprint(pedagogie_bp)
    app.register_blueprint(quartiers_bp)
    app.register_blueprint(partenaires_bp)
    app.register_blueprint(questionnaires_bp)

    @app.before_request
    def _ensure_initial_setup():
        from app.models import User

        endpoint = (request.endpoint or "")
        if endpoint.startswith("static") or endpoint.startswith("setup.") or endpoint in {"media_file", "healthz"}:
            return None

        if User.query.count() == 0:
            return redirect(url_for("setup.wizard"))

        return None

    # ------------------------------------------------------------------
    # RBAC helpers
    # ------------------------------------------------------------------
    from app.rbac import bootstrap_rbac, can

    @app.context_processor
    def _inject_rbac_helpers():
        return {"can": can}

    @app.context_processor
    def inject_app_identity():
        from app.services.instance_settings import resolve_identity
        from app.services.storage import media_url

        app_name, organization_name, app_logo_path, organization_logo_path = resolve_identity(
            app.config.get("APP_NAME", "App Gestion"),
            app.config.get("ORGANIZATION_NAME", "Votre structure"),
        )
        return {
            "APP_NAME": app_name,
            "ORGANIZATION_NAME": organization_name,
            "APP_LOGO_URL": media_url(app_logo_path) if app_logo_path else None,
            "ORGANIZATION_LOGO_URL": media_url(organization_logo_path) if organization_logo_path else None,
            "media_url": media_url,
        }

    # ------------------------------------------------------------------
    # ensure_schema : migrations légères SQLite / Postgres
    # ------------------------------------------------------------------
    def ensure_schema():
        dialect = db.engine.dialect.name
        insp = inspect(db.engine)

        def has_table(name):
            try:
                return insp.has_table(name)
            except Exception:
                return False

        def get_cols(table):
            if not has_table(table):
                return set()
            return {c["name"] for c in insp.get_columns(table)}

        def exec_sql(sql):
            db.session.execute(text(sql))

        def add_col(table, col, sql_sqlite, sql_pg):
            if col in get_cols(table):
                return
            if dialect == "sqlite":
                exec_sql(sql_sqlite)
            else:
                exec_sql(sql_pg)

        # --------------------------------------------------------------
        # 0) LEGACY : colonne user.role (OBLIGATOIRE pour le boot)
        # --------------------------------------------------------------
        try:
            add_col(
                "user",
                "role",
                'ALTER TABLE "user" ADD COLUMN role VARCHAR(50) NOT NULL DEFAULT "responsable_secteur"',
                'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS role VARCHAR(50) NOT NULL DEFAULT \'responsable_secteur\'',
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

        # --------------------------------------------------------------
        # 1) Exemple : colonne nature sur ligne_budget
        # --------------------------------------------------------------
        try:
            add_col(
                "ligne_budget",
                "nature",
                "ALTER TABLE ligne_budget ADD COLUMN nature VARCHAR(10) NOT NULL DEFAULT 'charge'",
                "ALTER TABLE ligne_budget ADD COLUMN IF NOT EXISTS nature VARCHAR(10) NOT NULL DEFAULT 'charge'",
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

        # --------------------------------------------------------------
        # 2) Quartiers: description libre
        # --------------------------------------------------------------
        try:
            add_col(
                "quartier",
                "description",
                "ALTER TABLE quartier ADD COLUMN description TEXT",
                "ALTER TABLE quartier ADD COLUMN IF NOT EXISTS description TEXT",
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

        # --------------------------------------------------------------
        # 3) Bilans lourds : médias + frise chronologique
        # --------------------------------------------------------------
        try:
            add_col(
                "bilan_lourd_narratif",
                "photos_json",
                "ALTER TABLE bilan_lourd_narratif ADD COLUMN photos_json TEXT",
                "ALTER TABLE bilan_lourd_narratif ADD COLUMN IF NOT EXISTS photos_json TEXT",
            )
            add_col(
                "bilan_lourd_narratif",
                "timeline_json",
                "ALTER TABLE bilan_lourd_narratif ADD COLUMN timeline_json TEXT",
                "ALTER TABLE bilan_lourd_narratif ADD COLUMN IF NOT EXISTS timeline_json TEXT",
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

    # ------------------------------------------------------------------
    # INIT DB (mode migration industrialisée)
    # ------------------------------------------------------------------
    with app.app_context():
        if app.config.get("DB_AUTO_UPGRADE_ON_START", True):
            try:
                from flask_migrate import upgrade, stamp

                insp_pre = inspect(db.engine)
                tables = set(insp_pre.get_table_names())
                has_legacy_core = {"user", "role", "permission", "atelier_activite"}.issubset(tables)
                if has_legacy_core and "alembic_version" not in tables:
                    app.logger.warning(
                        "Base existante détectée sans alembic_version: stamp(head) avant upgrade."
                    )
                    stamp(revision="head")

                upgrade()
            except Exception:
                app.logger.exception("Echec db upgrade au démarrage")
                raise

        if app.config.get("DB_ENABLE_LEGACY_SCHEMA_PATCH", False):
            ensure_schema()

        insp = inspect(db.engine)
        if insp.has_table("user") and insp.has_table("role") and insp.has_table("permission"):
            bootstrap_rbac()

            from app.secteurs import bootstrap_secteurs_from_config
            bootstrap_secteurs_from_config()

        print("DB URI =", db.engine.url)
        print("DB DIALECT =", db.engine.dialect.name)

        @app.context_processor
        def inject_secteurs():
            # Secteurs canoniques pour les formulaires.
            # Source: DB (Secteur) avec fallback config.
            from app.secteurs import get_secteur_labels
            return {"SECTEURS": get_secteur_labels(active_only=True)}


        return app
