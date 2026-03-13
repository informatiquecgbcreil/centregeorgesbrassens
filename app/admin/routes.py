import os
import tempfile
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app, jsonify
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import User, Role, Permission, Secteur, InstanceSettings, Framework, Skill
from app.utils.delete_guard import commit_delete
from app.rbac import require_perm
from app.services.storage import ensure_upload_subdir, media_relpath
from app.ateliers.excel_import import import_presences_from_xlsx

bp = Blueprint("admin", __name__, url_prefix="/admin")



def _get_single_role_code_from_form() -> str | None:
    """Accept both old multi-select ('role_codes') and new single-select ('role_code' or 'roles')."""
    # Old UI: <select multiple name="role_codes">
    role_codes = request.form.getlist("role_codes")
    if role_codes:
        # take the first one deterministically
        return (role_codes[0] or "").strip() or None

    # New UI possibilities
    for key in ("role_code", "roles", "role"):
        v = (request.form.get(key) or "").strip()
        if v:
            return v
    return None


@bp.route("/users", methods=["GET", "POST"])
@login_required
@require_perm("admin:users")
def users():
    from app.models import User, Role
    from app.secteurs import get_secteur_labels
    from app.extensions import db

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        nom = request.form.get("nom", "").strip()
        password = request.form.get("password", "")
        role_code = request.form.get("role")
        secteur = request.form.get("secteur_assigne") or None

        if not email or not password or not role_code:
            flash("Certains champs obligatoires sont manquants.", "danger")
            return redirect(url_for("admin.users"))

        if User.query.filter_by(email=email).first():
            flash("Un utilisateur utilisant cette adresse e-mail existe déjà.", "danger")
            return redirect(url_for("admin.users"))

        u = User(email=email, nom=nom or "Utilisateur")
        u.set_password(password)
        u.secteur_assigne = secteur

        role = Role.query.filter_by(code=role_code).first()
        if role:
            u.roles.append(role)

        db.session.add(u)
        db.session.commit()

        flash("Le compte utilisateur a bien été créé.", "success")
        return redirect(url_for("admin.users"))

    users = User.query.order_by(User.nom).all()
    roles = Role.query.order_by(Role.code).all()
    secteurs = get_secteur_labels(active_only=True)

    return render_template(
        "admin_users.html",
        users=users,
        roles=roles,
        secteurs=secteurs,
    )


@bp.route("/delete/<int:user_id>", methods=["POST"])
@login_required
@require_perm("admin:users")
def delete_user(user_id):
    if current_user.id == user_id:
        flash("Vous ne pouvez pas supprimer votre propre compte.", "danger")
        return redirect(url_for("admin.users"))

    u = User.query.get_or_404(user_id)
    db.session.delete(u)
    commit_delete(
        f"l'utilisateur « {u.nom} »",
        "Le compte utilisateur a bien été supprimé.",
        success_category="warning",
        blocked_message=f"Impossible de supprimer l'utilisateur « {u.nom} » : il est encore référencé ailleurs.",
    )
    return redirect(url_for("admin.users"))


@bp.route("/droits", methods=["GET", "POST"])
@login_required
@require_perm("admin:rbac")
def droits():
    """UI RBAC: attribuer des rôles à un user, et éditer les perms d'un rôle."""

    roles = Role.query.order_by(Role.code.asc()).all()
    perms = Permission.query.order_by(Permission.category.asc(), Permission.code.asc()).all()
    users_list = User.query.order_by(User.nom.asc()).all()

    if request.method == "POST":
        action = request.form.get("action")

        # --- Affecter un rôle (unique) à un user ---
        if action == "set_user_roles":
            user_id = int(request.form.get("user_id") or 0)
            u = User.query.get_or_404(user_id)

            role_code = _get_single_role_code_from_form()

            # Force: 1 seul rôle RBAC
            u.roles = []
            if role_code:
                r = Role.query.filter_by(code=role_code).first()
                if r:
                    u.roles.append(r)
                    # legacy sync

            db.session.commit()
            flash("Les rôles de l’utilisateur ont bien été mis à jour.", "success")
            return redirect(url_for("admin.droits"))

        # --- Affecter des permissions à un rôle ---
        if action == "set_role_perms":
            role_code = (request.form.get("role_code") or "").strip()
            perm_codes = set(request.form.getlist("perm_codes"))

            role = Role.query.filter_by(code=role_code).first_or_404()
            role.permissions = []
            for pcode in perm_codes:
                p = Permission.query.filter_by(code=pcode).first()
                if p:
                    role.permissions.append(p)

            db.session.commit()
            flash("Les permissions du rôle ont bien été mises à jour.", "success")
            return redirect(url_for("admin.droits"))

    return render_template(
        "admin_droits.html",
        roles=roles,
        perms=perms,
        users_list=users_list,
        users=users_list,
    )


# ---------------------------------------------------------------------------
# RBAC: endpoints attendus par le template admin_droits.html
# ---------------------------------------------------------------------------

@bp.route("/set_user_roles", methods=["POST"])
@login_required
@require_perm("admin:users")
@require_perm("admin:rbac")
def set_user_roles():
    user_id = int(request.form.get("user_id") or 0)
    u = User.query.get_or_404(user_id)

    role_code = _get_single_role_code_from_form()

    # Force: 1 seul rôle RBAC
    u.roles = []
    if role_code:
        r = Role.query.filter_by(code=role_code).first()
        if r:
            u.roles.append(r)
            # legacy sync

    db.session.commit()
    flash("Les rôles de l’utilisateur ont bien été mis à jour.", "success")
    return redirect(url_for("admin.droits"))


@bp.route("/save_role_perms", methods=["POST"])
@login_required
@require_perm("admin:rbac")
def save_role_perms():
    role_code = (request.form.get("role_code") or "").strip()
    perm_codes = set(request.form.getlist("perm_codes"))

    role = Role.query.filter_by(code=role_code).first_or_404()
    role.permissions = []
    for pcode in perm_codes:
        p = Permission.query.filter_by(code=pcode).first()
        if p:
            role.permissions.append(p)

    db.session.commit()
    flash("Les permissions du rôle ont bien été mises à jour.", "success")
    return redirect(url_for("admin.droits"))


@bp.route("/create_role", methods=["POST"])
@login_required
@require_perm("admin:rbac")
def create_role():
    code = (request.form.get("code") or "").strip()
    label = (request.form.get("label") or "").strip() or None

    if not code:
        flash("Le code du rôle est obligatoire.", "danger")
        return redirect(url_for("admin.droits"))

    if Role.query.filter_by(code=code).first():
        flash("Un rôle avec ce code existe déjà.", "warning")
        return redirect(url_for("admin.droits"))

    r = Role(code=code, label=label)
    db.session.add(r)
    db.session.commit()

    flash("Le rôle a bien été créé.", "success")
    return redirect(url_for("admin.droits"))


@bp.route("/delete_role", methods=["POST"])
@login_required
@require_perm("admin:rbac")
def delete_role():
    role_code = (request.form.get("role_code") or "").strip()
    if not role_code:
        flash("Sélectionnez un rôle à supprimer.", "danger")
        return redirect(url_for("admin.droits"))

    r = Role.query.filter_by(code=role_code).first()
    if not r:
        flash("Le rôle demandé est introuvable.", "warning")
        return redirect(url_for("admin.droits"))

    # Détache users + perms (évite erreurs tables d'association)
    for u in User.query.all():
        if hasattr(u, "roles") and r in u.roles:
            u.roles.remove(r)
    r.permissions = []

    db.session.delete(r)
    commit_delete(
        f"le rôle « {r.code} »",
        "Le rôle a bien été supprimé.",
        success_category="warning",
        blocked_message=f"Impossible de supprimer le rôle « {r.code} » : il est encore utilisé ailleurs.",
    )
    return redirect(url_for("admin.droits"))


@bp.route("/get_role_perms/<role_code>", methods=["GET"])
@login_required
@require_perm("admin:rbac")
def get_role_perms(role_code):
    r = Role.query.filter_by(code=role_code).first()
    if not r:
        return jsonify({"role": role_code, "perms": []})

    perms = [p.code for p in (r.permissions or [])]
    return jsonify({"role": r.code, "perms": perms})




# ------------------------------------------------------------------
# Secteurs (admin)
# ------------------------------------------------------------------

@bp.route("/secteurs", methods=["GET", "POST"])
@login_required
@require_perm("secteurs:edit")
def secteurs():
    """Page d'administration des secteurs."""
    from app.secteurs import upsert_secteur

    if request.method == "POST":
        label = (request.form.get("label") or "").strip()
        code = (request.form.get("code") or "").strip() or None
        try:
            upsert_secteur(label=label, code=code, is_active=True)
            flash("Secteur créé / mis à jour ✅", "success")
        except Exception as e:
            current_app.logger.exception("Erreur création secteur")
            flash(f"Impossible de créer / mettre à jour : {e}", "danger")
        return redirect(url_for("admin.secteurs"))

    secteurs = Secteur.query.order_by(Secteur.label.asc()).all()
    return render_template("admin_secteurs.html", secteurs=secteurs)


@bp.route("/secteurs/<int:secteur_id>/rename", methods=["POST"])
@login_required
@require_perm("secteurs:edit")
def secteur_rename(secteur_id: int):
    s = Secteur.query.get_or_404(secteur_id)
    new_label = (request.form.get("label") or "").strip()
    if not new_label:
        flash("Nom de secteur vide ❌", "danger")
        return redirect(url_for("admin.secteurs"))

    # On garde le code stable (slug) : on ne change que le label.
    s.label = new_label
    try:
        db.session.commit()
        flash("Secteur renommé ✅", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erreur renommage secteur")
        flash(f"Impossible de renommer : {e}", "danger")
    return redirect(url_for("admin.secteurs"))


@bp.route("/secteurs/<int:secteur_id>/toggle", methods=["POST"])
@login_required
@require_perm("secteurs:edit")
def secteur_toggle(secteur_id: int):
    s = Secteur.query.get_or_404(secteur_id)
    s.is_active = not bool(s.is_active)
    try:
        db.session.commit()
        flash("Le statut du secteur a bien été mis à jour.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Erreur toggle secteur")
        flash(f"Impossible de modifier le statut : {e}", "danger")
    return redirect(url_for("admin.secteurs"))


@bp.route('/debug_rbac', methods=['GET'])
@login_required
@require_perm("admin:rbac")
@require_perm('admin:rbac')
def debug_rbac():
    # Liste des permissions effectives pour l'utilisateur courant (debug)
    perms=set()
    for r in getattr(current_user,'roles',[]) or []:
        for p in getattr(r,'permissions',[]) or []:
            perms.add(p.code)
    return render_template('admin_debug_rbac.html', perms=sorted(perms))


@bp.route("/instance", methods=["GET", "POST"])
@login_required
@require_perm("admin:rbac")
def instance_settings():
    row = InstanceSettings.query.first() or InstanceSettings()

    if request.method == "POST":
        app_name = (request.form.get("app_name") or "").strip()
        org_name = (request.form.get("organization_name") or "").strip()
        public_base_url = (request.form.get("public_base_url") or "").strip() or None

        smtp_host = (request.form.get("smtp_host") or "").strip() or None
        smtp_port_raw = (request.form.get("smtp_port") or "").strip()
        smtp_port = int(smtp_port_raw) if smtp_port_raw.isdigit() else None
        smtp_username = (request.form.get("smtp_username") or "").strip() or None
        smtp_password = (request.form.get("smtp_password") or "").strip() or None
        smtp_sender = (request.form.get("smtp_sender") or "").strip() or None
        smtp_use_tls = request.form.get("smtp_use_tls") in {"1", "true", "on", "yes", "YES"}

        if smtp_host and (not smtp_port or smtp_port <= 0):
            flash("Port SMTP invalide.", "danger")
            return redirect(url_for("admin.instance_settings"))
        if smtp_host and not smtp_sender:
            flash("Adresse expéditeur SMTP requise.", "danger")
            return redirect(url_for("admin.instance_settings"))

        if not row.id:
            db.session.add(row)

        row.app_name = app_name or None
        row.organization_name = org_name or None
        row.public_base_url = public_base_url
        row.smtp_host = smtp_host
        row.smtp_port = smtp_port
        row.smtp_username = smtp_username
        if smtp_password:
            row.smtp_password = smtp_password
        row.smtp_sender = smtp_sender
        row.smtp_use_tls = smtp_use_tls if smtp_host else None

        logo_dir = ensure_upload_subdir("branding")

        app_logo = request.files.get("app_logo")
        if app_logo and app_logo.filename:
            name = secure_filename(app_logo.filename)
            ext = os.path.splitext(name)[1].lower()
            if ext in {".png", ".jpg", ".jpeg", ".webp", ".svg"}:
                out = f"app_logo{ext}"
                app_logo.save(os.path.join(logo_dir, out))
                row.app_logo_path = media_relpath("branding", out)

        org_logo = request.files.get("organization_logo")
        if org_logo and org_logo.filename:
            name = secure_filename(org_logo.filename)
            ext = os.path.splitext(name)[1].lower()
            if ext in {".png", ".jpg", ".jpeg", ".webp", ".svg"}:
                out = f"organization_logo{ext}"
                org_logo.save(os.path.join(logo_dir, out))
                row.organization_logo_path = media_relpath("branding", out)

        db.session.commit()
        flash("Paramètres de votre structure enregistrés ✅", "success")
        return redirect(url_for("admin.instance_settings"))

    return render_template("admin_instance.html", settings=row)



@bp.route("/import-excel", methods=["GET", "POST"])
@login_required
@require_perm("ateliers:sync")
def import_excel():
    """Import Excel des présences (format tableaux Antoine)."""
    if request.method == "GET":
        return render_template("admin_import_excel.html")

    f = request.files.get("xlsx_file")
    secteur = (request.form.get("secteur") or "NUMERIQUE").strip() or "NUMERIQUE"
    dry_run = (request.form.get("dry_run") in ("1", "true", "on", "yes"))

    if not f or not f.filename:
        flash("Fichier manquant.", "danger")
        return redirect(url_for("admin.import_excel"))

    filename = secure_filename(f.filename)
    tmpdir = tempfile.mkdtemp(prefix="import_excel_")
    tmp_path = os.path.join(tmpdir, filename)
    f.save(tmp_path)

    try:
        stats = import_presences_from_xlsx(tmp_path, secteur=secteur, dry_run=dry_run)
        if dry_run:
            flash("Dry-run OK : rien n'a été enregistré (rollback).", "info")
        else:
            flash("Import terminé ✅", "success")
        return render_template("admin_import_excel.html", stats=stats, secteur=secteur, dry_run=dry_run)
    except Exception as e:
        flash(f"Erreur import : {e}", "danger")
        return render_template("admin_import_excel.html", secteur=secteur, dry_run=dry_run)
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        try:
            os.rmdir(tmpdir)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Référentiels / Compétences (DigComp, Pix, CléA...) - import CSV
# ---------------------------------------------------------------------------

@bp.route("/referentiels")
@login_required
@require_perm("admin:rbac")
def referentiels():
    # Nombre de compétences par framework
    frameworks = Framework.query.order_by(Framework.code.asc()).all()
    out = []
    for fw in frameworks:
        cnt = Skill.query.filter_by(framework_id=fw.id).count()
        out.append({
            "id": fw.id,
            "code": fw.code,
            "nom": fw.nom,
            "version": fw.version,
            "lang": fw.lang,
            "actif": fw.actif,
            "skill_count": cnt,
        })
    return render_template("admin_referentiels.html", frameworks=out)


@bp.route("/referentiels/import", methods=["GET", "POST"])
@login_required
@require_perm("admin:rbac")
def import_skills():
    preset_code = (request.args.get("framework_code") or "").strip() or None

    if request.method == "POST":
        fw_code = (request.form.get("framework_code") or "").strip()
        fw_name = (request.form.get("framework_name") or "").strip()
        fw_version = (request.form.get("framework_version") or "").strip() or None
        fw_lang = (request.form.get("framework_lang") or "").strip() or "fr"
        source_url = (request.form.get("source_url") or "").strip() or None

        f = request.files.get("csv_file")
        if not fw_code or not fw_name or not f:
            flash("Champs manquants (code, nom, CSV).", "danger")
            return redirect(url_for("admin.import_skills", framework_code=fw_code or ""))

        # Enregistrer temporairement (Windows-friendly) puis lire en utf-8-sig
        tmpdir = tempfile.mkdtemp(prefix="skills_import_")
        filename = secure_filename(f.filename or "skills.csv")
        csv_path = os.path.join(tmpdir, filename)
        f.save(csv_path)

        import csv as _csv

        fw = Framework.query.filter_by(code=fw_code).first()
        if not fw:
            fw = Framework(code=fw_code, nom=fw_name, version=fw_version, lang=fw_lang, source_url=source_url, actif=True)
            db.session.add(fw)
            db.session.flush()
        else:
            fw.nom = fw_name
            fw.version = fw_version
            fw.lang = fw_lang
            fw.source_url = source_url

        created = 0
        updated = 0

        with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = _csv.DictReader(fh)
            required = {"code", "label"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                flash(f"CSV invalide : colonnes manquantes {sorted(missing)}", "danger")
                return redirect(url_for("admin.import_skills", framework_code=fw_code))

            for row in reader:
                code = (row.get("code") or "").strip()
                if not code:
                    continue

                sk = Skill.query.filter_by(framework_id=fw.id, code=code).first()
                if not sk:
                    sk = Skill(framework_id=fw.id, code=code, label=(row.get('label') or '').strip() or code)
                    db.session.add(sk)
                    created += 1
                else:
                    updated += 1

                sk.label = (row.get("label") or "").strip() or sk.label
                sk.description = (row.get("description") or "").strip() or None
                sk.domain_code = (row.get("domain_code") or "").strip() or None
                sk.domain_label = (row.get("domain_label") or "").strip() or None

                so = (row.get("sort_order") or "").strip()
                if so.isdigit():
                    sk.sort_order = int(so)

                actif_raw = (row.get("actif") or "").strip().lower()
                if actif_raw in {"0", "false", "non", "n"}:
                    sk.actif = False
                elif actif_raw in {"1", "true", "oui", "o", "y", "yes"}:
                    sk.actif = True

        db.session.commit()
        flash(f"Import OK : {created} créées, {updated} mises à jour (framework {fw.code}).", "success")
        return redirect(url_for("admin.referentiels"))

    return render_template("admin_import_skills.html", preset_code=preset_code)
