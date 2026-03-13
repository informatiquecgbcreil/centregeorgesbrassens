import csv
import datetime
import mimetypes
import os
from collections import defaultdict
from io import StringIO

from flask import Response, current_app, flash, redirect, render_template, request, send_file, url_for
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import (
    AtelierActivite,
    Competence,
    Evaluation,
    Objectif,
    ObjectifCompetenceMap,
    atelier_competence,
    module_competence,
    objectif_competence,
    projet_competence,
    session_competence,
    ObjectifSuivi,
    PasseportNote,
    PasseportPieceJointe,
    PedagogieModule,
    PlanProjetAtelierModule,
    PresenceActivite,
    Projet,
    ProjetAtelier,
    Referentiel,
    SessionActivite,
)
from app.rbac import require_perm

from . import bp
from app.utils.delete_guard import commit_delete
from .services import compute_objectif_scores, participant_timeline


PASSPORT_NOTE_CATEGORIES = {
    "journal": "Journal",
    "participation": "Participation",
    "progression": "Progression",
    "temoignage": "Témoignage",
    "session": "Session",
}


def _normalize_note_category(raw: str | None) -> str:
    val = (raw or "journal").strip().lower()
    return val if val in PASSPORT_NOTE_CATEGORIES else "journal"


# ============================================================
# Référentiels
# ============================================================

@bp.route("/referentiels", methods=["GET", "POST"])
@login_required
@require_perm("pedagogie:view")
def referentiels_list():
    if request.method == "POST":
        action = request.form.get("action") or ""
        if action == "create_referentiel":
            nom = (request.form.get("nom") or "").strip()
            description = (request.form.get("description") or "").strip() or None
            if not nom:
                flash("Nom du référentiel obligatoire.", "danger")
                return redirect(url_for("pedagogie.referentiels_list"))
            ref = Referentiel(nom=nom, description=description)
            db.session.add(ref)
            db.session.commit()
            flash("Référentiel créé.", "success")
            return redirect(url_for("pedagogie.referentiels_list"))

        if action == "delete_referentiel":
            ref_id = int(request.form.get("referentiel_id") or 0)
            ref = Referentiel.query.get_or_404(ref_id)
            comp_ids = [c.id for c in Competence.query.filter_by(referentiel_id=ref.id).all()]
            usage = {}
            if comp_ids:
                usage = {
                    "compétences": len(comp_ids),
                    "évaluations": Evaluation.query.filter(Evaluation.competence_id.in_(comp_ids)).count(),
                    "objectifs": ObjectifCompetenceMap.query.filter(ObjectifCompetenceMap.competence_id.in_(comp_ids)).count(),
                    "projets": db.session.execute(
                        db.select(db.func.count()).select_from(projet_competence).where(projet_competence.c.competence_id.in_(comp_ids))
                    ).scalar_one(),
                    "ateliers": db.session.execute(
                        db.select(db.func.count()).select_from(atelier_competence).where(atelier_competence.c.competence_id.in_(comp_ids))
                    ).scalar_one(),
                    "sessions": db.session.execute(
                        db.select(db.func.count()).select_from(session_competence).where(session_competence.c.competence_id.in_(comp_ids))
                    ).scalar_one(),
                    "modules": db.session.execute(
                        db.select(db.func.count()).select_from(module_competence).where(module_competence.c.competence_id.in_(comp_ids))
                    ).scalar_one(),
                    "objectifs_m2m": db.session.execute(
                        db.select(db.func.count()).select_from(objectif_competence).where(objectif_competence.c.competence_id.in_(comp_ids))
                    ).scalar_one(),
                }
            blocking = {label: count for label, count in usage.items() if count and label != "compétences"}
            if blocking:
                details = ", ".join(f"{count} {label}" for label, count in blocking.items())
                flash(
                    f"Impossible de supprimer le référentiel « {ref.nom} » : il est encore utilisé ({details}). "
                    "Retire d'abord ses compétences des évaluations, modules, ateliers, sessions, projets ou objectifs concernés.",
                    "danger",
                )
                return redirect(url_for("pedagogie.referentiels_list"))
            if comp_ids:
                db.session.execute(delete(projet_competence).where(projet_competence.c.competence_id.in_(comp_ids)))
                db.session.execute(delete(atelier_competence).where(atelier_competence.c.competence_id.in_(comp_ids)))
                db.session.execute(delete(session_competence).where(session_competence.c.competence_id.in_(comp_ids)))
                db.session.execute(delete(module_competence).where(module_competence.c.competence_id.in_(comp_ids)))
                db.session.execute(delete(objectif_competence).where(objectif_competence.c.competence_id.in_(comp_ids)))
            db.session.delete(ref)
            if commit_delete(
                f"le référentiel « {ref.nom} »",
                "Référentiel supprimé.",
                success_category="warning",
                blocked_message=f"Impossible de supprimer le référentiel « {ref.nom} » : des éléments y sont encore rattachés.",
            ):
                return redirect(url_for("pedagogie.referentiels_list"))
            return redirect(url_for("pedagogie.referentiels_list"))

    referentiels = Referentiel.query.order_by(Referentiel.nom.asc()).all()
    return render_template("pedagogie/referentiels.html", referentiels=referentiels)


@bp.route("/referentiels/<int:referentiel_id>", methods=["GET", "POST"])
@login_required
@require_perm("pedagogie:view")
def referentiels_edit(referentiel_id: int):
    referentiel = Referentiel.query.get_or_404(referentiel_id)

    if request.method == "POST":
        action = request.form.get("action") or ""

        if action == "update_referentiel":
            referentiel.nom = (request.form.get("nom") or "").strip()
            referentiel.description = (request.form.get("description") or "").strip() or None
            if not referentiel.nom:
                flash("Nom obligatoire.", "danger")
                return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))
            db.session.commit()
            flash("Référentiel mis à jour.", "success")
            return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

        if action == "add_competence":
            code = (request.form.get("code") or "").strip()
            nom = (request.form.get("nom") or "").strip()
            description = (request.form.get("description") or "").strip() or None
            if not code or not nom:
                flash("Code et nom de compétence obligatoires.", "danger")
                return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))
            comp = Competence(
                referentiel_id=referentiel.id,
                code=code,
                nom=nom,
                description=description,
            )
            db.session.add(comp)
            db.session.commit()
            flash("Compétence ajoutée.", "success")
            return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

        if action == "delete_competence":
            comp_id = int(request.form.get("competence_id") or 0)
            comp = Competence.query.get_or_404(comp_id)
            if comp.referentiel_id != referentiel.id:
                flash("Compétence invalide.", "danger")
                return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

            usage = {
                "évaluations": Evaluation.query.filter_by(competence_id=comp.id).count(),
                "objectifs": ObjectifCompetenceMap.query.filter_by(competence_id=comp.id).count(),
                "projets": db.session.execute(
                    db.select(db.func.count()).select_from(projet_competence).where(projet_competence.c.competence_id == comp.id)
                ).scalar_one(),
                "ateliers": db.session.execute(
                    db.select(db.func.count()).select_from(atelier_competence).where(atelier_competence.c.competence_id == comp.id)
                ).scalar_one(),
                "sessions": db.session.execute(
                    db.select(db.func.count()).select_from(session_competence).where(session_competence.c.competence_id == comp.id)
                ).scalar_one(),
                "modules": db.session.execute(
                    db.select(db.func.count()).select_from(module_competence).where(module_competence.c.competence_id == comp.id)
                ).scalar_one(),
                "objectifs_m2m": db.session.execute(
                    db.select(db.func.count()).select_from(objectif_competence).where(objectif_competence.c.competence_id == comp.id)
                ).scalar_one(),
            }
            blocking = {label: count for label, count in usage.items() if count}
            if blocking:
                details = ", ".join(f"{count} {label}" for label, count in blocking.items())
                flash(
                    f"Impossible de supprimer la compétence « {comp.code} - {comp.nom} » : elle est encore utilisée ({details}). "
                    "Retire-la d'abord des évaluations, modules, ateliers, sessions ou objectifs concernés.",
                    "danger",
                )
                return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

            db.session.execute(delete(projet_competence).where(projet_competence.c.competence_id == comp.id))
            db.session.execute(delete(atelier_competence).where(atelier_competence.c.competence_id == comp.id))
            db.session.execute(delete(session_competence).where(session_competence.c.competence_id == comp.id))
            db.session.execute(delete(module_competence).where(module_competence.c.competence_id == comp.id))
            db.session.execute(delete(objectif_competence).where(objectif_competence.c.competence_id == comp.id))
            db.session.delete(comp)
            if commit_delete(
                f"la compétence « {comp.code} - {comp.nom} »",
                "Compétence supprimée.",
                success_category="warning",
                blocked_message=f"Impossible de supprimer la compétence « {comp.code} - {comp.nom} » : elle est encore utilisée ailleurs.",
            ):
                return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))
            return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

    competences = Competence.query.filter_by(referentiel_id=referentiel.id).order_by(Competence.code.asc()).all()
    return render_template(
        "pedagogie/referentiel_edit.html",
        referentiel=referentiel,
        competences=competences,
    )




@bp.route("/referentiels/<int:referentiel_id>/import_csv", methods=["POST"])
@login_required
@require_perm("pedagogie:view")
def import_referentiel_csv(referentiel_id: int):
    """Import CSV de compétences dans un référentiel (upsert par code).

    Colonnes attendues (au minimum):
    - code
    - nom (ou label)
    Colonnes optionnelles:
    - description

    Les colonnes supplémentaires (ex: domain_code, sort_order...) sont ignorées.
    """
    referentiel = Referentiel.query.get_or_404(referentiel_id)

    f = request.files.get("csv_file")
    if not f or not f.filename:
        flash("Fichier CSV manquant.", "danger")
        return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

    filename = secure_filename(f.filename)
    if not filename.lower().endswith(".csv"):
        flash("Le fichier doit être un .csv", "danger")
        return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

    # Lecture en mémoire : UTF-8 BOM puis fallback Windows-1252/Latin-1
    raw = f.read()
    try:
        content = raw.decode("utf-8-sig")
    except Exception:
        try:
            content = raw.decode("cp1252")
        except Exception:
            content = raw.decode("latin-1")

    reader = csv.DictReader(StringIO(content))
    headers = [h.strip() for h in (reader.fieldnames or []) if h]
    headers_lower = [h.lower() for h in headers]

    if "code" not in headers_lower:
        flash("CSV invalide : colonne 'code' manquante.", "danger")
        return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

    # nom peut s'appeler 'nom' ou 'label' (DigComp)
    has_nom = "nom" in headers_lower or "label" in headers_lower
    if not has_nom:
        flash("CSV invalide : colonne 'nom' ou 'label' manquante.", "danger")
        return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

    created = 0
    updated = 0
    skipped = 0

    # index existants par code (case-insensitive)
    existing = {c.code.strip().lower(): c for c in Competence.query.filter_by(referentiel_id=referentiel.id).all()}

    header_count = len(reader.fieldnames or [])

    for i, row in enumerate(reader, start=2):
        # DictReader met les colonnes excédentaires sous la clé None sous forme de liste.
        # Cas typique : CSV exporté avec des virgules non protégées dans "description".
        extras = row.get(None) or []

        def _clean(value):
            if value is None:
                return ""
            if isinstance(value, list):
                return ",".join(str(x) for x in value if x is not None).strip()
            return str(value).strip()

        norm = {(k or "").strip().lower(): _clean(v) for k, v in row.items() if k is not None}

        # Rattrapage des lignes avec colonnes en trop :
        # code,label,description,domain_code,...
        # devient parfois description="Capacité à localiser", domain_code=" filtrer...", etc.
        if extras:
            ordered_values = [_clean(row.get(h)) for h in (reader.fieldnames or [])]
            if len(ordered_values) >= 3:
                fixed_desc = ",".join(v for v in [ordered_values[2], *[_clean(x) for x in extras]] if v).strip()
                norm["description"] = fixed_desc

        code = norm.get("code", "").strip()
        if not code:
            skipped += 1
            continue

        nom = (norm.get("nom") or norm.get("label") or "").strip()
        if not nom:
            skipped += 1
            continue

        desc = (norm.get("description") or "").strip() or None

        key = code.lower()
        if key in existing:
            c = existing[key]
            changed = False
            if c.nom != nom:
                c.nom = nom
                changed = True
            if (c.description or None) != desc:
                c.description = desc
                changed = True
            if changed:
                updated += 1
        else:
            c = Competence(referentiel_id=referentiel.id, code=code, nom=nom, description=desc)
            db.session.add(c)
            existing[key] = c
            created += 1

    db.session.commit()
    flash(f"Import terminé : {created} créées, {updated} mises à jour, {skipped} ignorées.", "success")
    return redirect(url_for("pedagogie.referentiels_edit", referentiel_id=referentiel.id))

# ============================================================
# Modules pédagogiques
# ============================================================

@bp.route("/modules", methods=["GET", "POST"])
@login_required
@require_perm("pedagogie:view")
def modules_pedagogiques():
    if request.method == "POST":
        action = request.form.get("action") or ""
        if action == "create_module":
            nom = (request.form.get("nom") or "").strip()
            description = (request.form.get("description") or "").strip() or None
            competence_ids = [int(cid) for cid in request.form.getlist("competence_ids") if cid.isdigit()]
            if not nom:
                flash("Nom du module obligatoire.", "danger")
                return redirect(url_for("pedagogie.modules_pedagogiques"))

            mod = PedagogieModule(nom=nom, description=description)
            if competence_ids:
                mod.competences = Competence.query.filter(Competence.id.in_(competence_ids)).all()

            db.session.add(mod)
            db.session.commit()
            flash("Module pédagogique créé.", "success")
            return redirect(url_for("pedagogie.modules_pedagogiques"))

    referentiels = Referentiel.query.order_by(Referentiel.nom.asc()).all()
    modules = PedagogieModule.query.filter(PedagogieModule.actif.is_(True)).order_by(PedagogieModule.nom.asc()).all()
    return render_template("pedagogie/modules.html", referentiels=referentiels, modules=modules)


# ============================================================
# Objectifs
# ============================================================

@bp.route("/objectifs", methods=["GET", "POST"])
@login_required
@require_perm("pedagogie:view")
def objectifs():
    projet_id = request.args.get("projet_id", type=int)
    atelier_id = request.args.get("atelier_id", type=int)
    session_id = request.args.get("session_id", type=int)

    def _create_objectif(*, obj_type: str, titre: str, description: str | None, seuil: float, parent_id: int | None, projet_id_val: int | None, atelier_id_val: int | None, module_id_val: int | None, session_id_val: int | None):
        obj = Objectif(
            type=obj_type,
            titre=titre,
            description=description,
            seuil_validation=seuil,
            parent_id=parent_id,
            projet_id=projet_id_val,
            atelier_id=atelier_id_val,
            session_id=session_id_val,
            module_id=module_id_val,
        )
        db.session.add(obj)
        db.session.commit()

        if obj.type == "operationnel" and obj.module_id:
            mod = PedagogieModule.query.get(obj.module_id)
            if mod:
                for comp in mod.competences:
                    existing = ObjectifCompetenceMap.query.filter_by(objectif_id=obj.id, competence_id=comp.id).first()
                    if not existing:
                        db.session.add(ObjectifCompetenceMap(objectif_id=obj.id, competence_id=comp.id, poids=1.0, actif=True))
                db.session.commit()
        return obj

    if request.method == "POST":
        action = request.form.get("action") or ""
        if action == "quick_create_parcours":
            selected_projet_id = request.form.get("projet_id", type=int)
            selected_atelier_id = request.form.get("atelier_id", type=int)
            selected_module_id = request.form.get("module_id", type=int)
            selected_session_id = request.form.get("session_id", type=int)
            titre_base = (request.form.get("titre_base") or "").strip()

            if not (selected_projet_id and selected_atelier_id and selected_module_id and titre_base):
                flash("Parcours express incomplet: projet, atelier, module et intitulé sont obligatoires.", "danger")
                return redirect(url_for("pedagogie.objectifs", projet_id=projet_id, atelier_id=atelier_id, session_id=session_id))

            atelier_obj = AtelierActivite.query.get(selected_atelier_id)
            module_obj = PedagogieModule.query.get(selected_module_id)
            if not atelier_obj or not module_obj:
                flash("Atelier ou module introuvable.", "danger")
                return redirect(url_for("pedagogie.objectifs", projet_id=projet_id, atelier_id=atelier_id, session_id=session_id))

            try:
                og = Objectif.query.filter_by(type="general", titre=titre_base, projet_id=selected_projet_id).first()
                if not og:
                    og = _create_objectif(
                        obj_type="general",
                        titre=titre_base,
                        description=None,
                        seuil=60.0,
                        parent_id=None,
                        projet_id_val=selected_projet_id,
                        atelier_id_val=None,
                        module_id_val=None,
                    
                        session_id_val=None,
                    )

                os_titre = f"{titre_base} · {atelier_obj.nom}"
                os_obj = Objectif.query.filter_by(type="specifique", titre=os_titre, atelier_id=selected_atelier_id, parent_id=og.id).first()
                if not os_obj:
                    os_obj = _create_objectif(
                        obj_type="specifique",
                        titre=os_titre,
                        description=None,
                        seuil=60.0,
                        parent_id=og.id,
                        projet_id_val=selected_projet_id,
                        atelier_id_val=selected_atelier_id,
                        module_id_val=None,
                    
                        session_id_val=None,
                    )

                oo_titre = f"{titre_base} · {module_obj.nom}"
                oo_obj = Objectif.query.filter_by(type="operationnel", titre=oo_titre, module_id=selected_module_id, parent_id=os_obj.id).first()
                if not oo_obj:
                    oo_obj = _create_objectif(
                        obj_type="operationnel",
                        titre=oo_titre,
                        description=None,
                        seuil=60.0,
                        parent_id=os_obj.id,
                        projet_id_val=selected_projet_id,
                        atelier_id_val=selected_atelier_id,
                        module_id_val=selected_module_id,
                    
                        session_id_val=None,
                    )
            except Exception as exc:
                db.session.rollback()
                current_app.logger.exception("Erreur création parcours express")
                flash(f"Impossible de créer le parcours express: {exc}", "danger")
                return redirect(url_for("pedagogie.objectifs", projet_id=selected_projet_id, atelier_id=selected_atelier_id))

            flash("Parcours express créé (OG + OS + OO).", "success")
            return redirect(url_for("pedagogie.objectifs", projet_id=selected_projet_id, atelier_id=selected_atelier_id))

        if action == "create_objectif":
            obj_type = (request.form.get("type") or "").strip()
            titre = (request.form.get("titre") or "").strip()
            description = (request.form.get("description") or "").strip() or None
            seuil_validation = request.form.get("seuil_validation", type=float) or 0.0
            parent_id = request.form.get("parent_id", type=int)
            selected_atelier_id = request.form.get("atelier_id", type=int)
            selected_projet_id = request.form.get("projet_id", type=int)
            selected_module_id = request.form.get("module_id", type=int)
            selected_session_id = request.form.get("session_id", type=int)

            parent_obj = Objectif.query.get(parent_id) if parent_id else None
            if parent_id and not parent_obj:
                flash("Objectif parent introuvable.", "danger")
                return redirect(url_for("pedagogie.objectifs", projet_id=projet_id, atelier_id=atelier_id, session_id=session_id))

            if not obj_type or not titre:
                flash("Type et titre obligatoires.", "danger")
                return redirect(url_for("pedagogie.objectifs", projet_id=projet_id, atelier_id=atelier_id, session_id=session_id))

            # Hérite du projet parent si non renseigné
            if parent_obj and not selected_projet_id and parent_obj.projet_id:
                selected_projet_id = parent_obj.projet_id

            if obj_type == "specifique" and not selected_projet_id and selected_atelier_id:
                link = ProjetAtelier.query.filter_by(atelier_id=selected_atelier_id).first()
                if link:
                    selected_projet_id = link.projet_id

            # Règles métier
            if obj_type == "general":
                if not selected_projet_id:
                    flash("Un objectif général doit être lié à un projet.", "danger")
                    return redirect(url_for("pedagogie.objectifs", projet_id=projet_id))
                selected_atelier_id = None
                selected_module_id = None
            elif obj_type == "specifique":
                if not selected_atelier_id:
                    flash("Un objectif spécifique doit être lié à un atelier.", "danger")
                    return redirect(url_for("pedagogie.objectifs", projet_id=projet_id, atelier_id=atelier_id))
                selected_module_id = None
            elif obj_type == "operationnel":
                if not selected_module_id:
                    flash("Un objectif opérationnel doit être lié à un module pédagogique.", "danger")
                    return redirect(url_for("pedagogie.objectifs", projet_id=projet_id, atelier_id=atelier_id))
            else:
                flash("Type d'objectif invalide.", "danger")
                return redirect(url_for("pedagogie.objectifs"))

            try:
                if obj_type == "specifique" and parent_obj and parent_obj.type != "general":
                    flash("Un objectif spécifique doit avoir un parent général.", "danger")
                    return redirect(url_for("pedagogie.objectifs", projet_id=selected_projet_id or projet_id, atelier_id=selected_atelier_id or atelier_id, session_id=session_id))
                if obj_type == "operationnel" and parent_obj and parent_obj.type != "specifique":
                    flash("Un objectif opérationnel doit avoir un parent spécifique.", "danger")
                    return redirect(url_for("pedagogie.objectifs", projet_id=selected_projet_id or projet_id, atelier_id=selected_atelier_id or atelier_id, session_id=session_id))

                obj = _create_objectif(
                    obj_type=obj_type,
                    titre=titre,
                    description=description,
                    seuil=seuil_validation,
                    parent_id=parent_id,
                    projet_id_val=selected_projet_id,
                    atelier_id_val=selected_atelier_id,
                    module_id_val=selected_module_id,
                    session_id_val=(selected_session_id if obj_type == 'operationnel' else None),
                )
            except Exception as exc:
                db.session.rollback()
                current_app.logger.exception("Erreur création objectif")
                flash(f"Impossible de créer l'objectif: {exc}", "danger")
                return redirect(url_for("pedagogie.objectifs", projet_id=selected_projet_id or projet_id, atelier_id=selected_atelier_id or atelier_id, session_id=session_id))

            flash("Objectif ajouté.", "success")
            return redirect(url_for("pedagogie.objectifs", projet_id=selected_projet_id, atelier_id=selected_atelier_id))

        if action == "delete_objectif":
            obj_id = int(request.form.get("objectif_id") or 0)
            obj = Objectif.query.get_or_404(obj_id)
            db.session.delete(obj)
            db.session.commit()
            flash("Objectif supprimé.", "warning")
            return redirect(url_for("pedagogie.objectifs", projet_id=projet_id, atelier_id=atelier_id, session_id=session_id))

    projets = Projet.query.order_by(Projet.secteur.asc(), Projet.nom.asc()).all()
    ateliers = AtelierActivite.query.filter(AtelierActivite.is_deleted.is_(False)).order_by(AtelierActivite.nom.asc()).all()
    sessions = SessionActivite.query.filter(SessionActivite.is_deleted.is_(False)).order_by(SessionActivite.created_at.desc()).all()
    referentiels = Referentiel.query.order_by(Referentiel.nom.asc()).all()
    modules = PedagogieModule.query.filter(PedagogieModule.actif.is_(True)).order_by(PedagogieModule.nom.asc()).all()

    objectifs_q = Objectif.query
    if projet_id:
        objectifs_q = objectifs_q.filter(Objectif.projet_id == projet_id)
    if atelier_id:
        objectifs_q = objectifs_q.filter(Objectif.atelier_id == atelier_id)
    if session_id:
        objectifs_q = objectifs_q.filter(Objectif.session_id == session_id)
    objectifs_rows = objectifs_q.order_by(Objectif.created_at.asc()).all()

    parent_options = Objectif.query.order_by(Objectif.created_at.asc()).all()

    return render_template(
        "pedagogie/objectifs.html",
        projets=projets,
        ateliers=ateliers,
        sessions=sessions,
        referentiels=referentiels,
        modules=modules,
        objectifs=objectifs_rows,
        parent_options=parent_options,
        projet_id=projet_id,
        atelier_id=atelier_id,
        session_id=session_id,
    )


# ============================================================
# Suivi / Stats
# ============================================================

@bp.route("/suivi")
@login_required
@require_perm("pedagogie:view")
def suivi_pedagogique():
    projets = Projet.query.order_by(Projet.nom.asc()).all()
    ateliers = AtelierActivite.query.filter_by(is_deleted=False).order_by(AtelierActivite.nom.asc()).all()

    projet_id = request.args.get("projet_id", type=int)
    atelier_id = request.args.get("atelier_id", type=int)

    total_competences = Competence.query.count()
    total_evaluations = Evaluation.query.count()

    return render_template(
        "stats_pedagogie.html",
        projets=projets,
        ateliers=ateliers,
        selected_projet=projet_id,
        selected_atelier=atelier_id,
        total_competences=total_competences,
        total_evaluations=total_evaluations,
    )


# ============================================================
# Kiosk
# ============================================================

@bp.route("/kiosk", methods=["GET", "POST"])
@login_required
@require_perm("pedagogie:view")
def kiosk_pedagogique():
    session_id = request.args.get("session_id", type=int)
    participant_id = request.args.get("participant_id", type=int)

    sessions = (
        SessionActivite.query.filter(SessionActivite.is_deleted.is_(False))
        .order_by(SessionActivite.created_at.desc())
        .limit(200)
        .all()
    )
    session = SessionActivite.query.get(session_id) if session_id else None

    participants = []
    if session:
        participants = (
            db.session.query(PresenceActivite)
            .filter(PresenceActivite.session_id == session.id)
            .join(PresenceActivite.participant)
            .order_by(PresenceActivite.created_at.asc())
            .all()
        )

    objectifs_rows = []
    if session:
        projet_ids = [
            pid for (pid,) in db.session.query(ProjetAtelier.projet_id).filter(ProjetAtelier.atelier_id == session.atelier_id).all()
        ]

        objectifs_generaux = []
        if projet_ids:
            objectifs_generaux = (
                Objectif.query.filter(Objectif.type == "general", Objectif.projet_id.in_(projet_ids))
                .order_by(Objectif.created_at.asc())
                .all()
            )
        objectifs_specifiques = (
            Objectif.query.filter(Objectif.type == "specifique", Objectif.atelier_id == session.atelier_id)
            .order_by(Objectif.created_at.asc())
            .all()
        )
        objectifs_operationnels = (
            Objectif.query.filter(Objectif.type == "operationnel", Objectif.session_id == session.id)
            .order_by(Objectif.created_at.asc())
            .all()
        )
        objectifs_rows = [*objectifs_generaux, *objectifs_specifiques, *objectifs_operationnels]

    if request.method == "POST" and session:
        mode = (request.form.get("mode") or "ressenti").strip()
        mode = mode if mode in {"ressenti", "competence", "mixte"} else "ressenti"

        participant_id_post = request.form.get("participant_id", type=int)
        objectif_ids = [int(x) for x in request.form.getlist("objectif_ids") if x.isdigit()]
        today = datetime.date.today()

        saved = 0
        for oid in objectif_ids:
            etat = request.form.get(f"etat_{oid}", type=int)
            ressenti = request.form.get(f"ressenti_{oid}", type=int)
            commentaire = (request.form.get(f"commentaire_{oid}") or "").strip() or None

            if etat is None and ressenti is None and commentaire is None:
                continue

            item = ObjectifSuivi.query.filter_by(
                objectif_id=oid,
                session_id=session.id,
                participant_id=participant_id_post,
                date_saisie=today,
            ).first()
            if not item:
                item = ObjectifSuivi(
                    objectif_id=oid,
                    session_id=session.id,
                    participant_id=participant_id_post,
                    date_saisie=today,
                    user_id=current_user.id,
                )
                db.session.add(item)

            item.mode = mode
            item.etat = etat if etat is not None else item.etat
            item.ressenti = ressenti if ressenti in (1, 2, 3, 4, 5) else None
            item.commentaire = commentaire
            item.user_id = current_user.id
            saved += 1

        db.session.commit()
        flash(f"{saved} suivi(s) objectif enregistré(s).", "success")
        return redirect(url_for("pedagogie.kiosk_pedagogique", session_id=session.id, participant_id=participant_id_post))

    recent_rows = []
    if session:
        q = ObjectifSuivi.query.filter(ObjectifSuivi.session_id == session.id)
        if participant_id:
            q = q.filter(ObjectifSuivi.participant_id == participant_id)
        recent_rows = q.order_by(ObjectifSuivi.updated_at.desc()).limit(40).all()

    return render_template(
        "pedagogie/kiosk.html",
        sessions=sessions,
        selected_session=session,
        participants=participants,
        selected_participant_id=participant_id,
        objectifs=objectifs_rows,
        recent_rows=recent_rows,
    )


# ============================================================
# Plan projet
# ============================================================

@bp.route("/plan_projet", methods=["GET", "POST"])
@login_required
@require_perm("pedagogie:view")
def plan_projet():
    if request.method == "POST":
        action = request.form.get("action") or ""
        if action == "add_link":
            projet_id = request.form.get("projet_id", type=int)
            atelier_id = request.form.get("atelier_id", type=int)
            module_id = request.form.get("module_id", type=int)
            if not projet_id or not atelier_id or not module_id:
                flash("Projet, atelier et module sont obligatoires.", "danger")
                return redirect(url_for("pedagogie.plan_projet"))
            row = PlanProjetAtelierModule.query.filter_by(projet_id=projet_id, atelier_id=atelier_id, module_id=module_id).first()
            if not row:
                db.session.add(PlanProjetAtelierModule(projet_id=projet_id, atelier_id=atelier_id, module_id=module_id, actif=True))
                db.session.commit()
            flash("Lien projet/atelier/module enregistré.", "success")
            return redirect(url_for("pedagogie.plan_projet", projet_id=projet_id))

        if action == "delete_link":
            row_id = request.form.get("row_id", type=int)
            row = PlanProjetAtelierModule.query.get_or_404(row_id)
            db.session.delete(row)
            db.session.commit()
            flash("Lien supprimé.", "warning")
            return redirect(url_for("pedagogie.plan_projet"))

    projet_id = request.args.get("projet_id", type=int)
    projets = Projet.query.order_by(Projet.nom.asc()).all()
    ateliers = AtelierActivite.query.filter(AtelierActivite.is_deleted.is_(False)).order_by(AtelierActivite.nom.asc()).all()
    modules = PedagogieModule.query.filter(PedagogieModule.actif.is_(True)).order_by(PedagogieModule.nom.asc()).all()

    rows_q = PlanProjetAtelierModule.query
    if projet_id:
        rows_q = rows_q.filter(PlanProjetAtelierModule.projet_id == projet_id)
    rows = rows_q.order_by(PlanProjetAtelierModule.created_at.desc()).all()

    return render_template("pedagogie/plan_projet.html", projets=projets, ateliers=ateliers, modules=modules, rows=rows, projet_id=projet_id)


# ============================================================
# Passeport
# ============================================================

@bp.route("/participant/<int:participant_id>/passeport")
@login_required
@require_perm("pedagogie:view")
def participant_passeport(participant_id: int):
    participant, events, current_levels = participant_timeline(participant_id)

    selected_secteur = (request.args.get("secteur") or "").strip()
    selected_categorie = _normalize_note_category(request.args.get("categorie")) if request.args.get("categorie") else ""
    notes_page = max(request.args.get("notes_page", 1, type=int), 1)
    files_page = max(request.args.get("files_page", 1, type=int), 1)
    page_size = 20

    presence_rows = (
        db.session.query(PresenceActivite)
        .join(SessionActivite, PresenceActivite.session_id == SessionActivite.id)
        .filter(PresenceActivite.participant_id == participant_id)
        .all()
    )
    sessions_by_secteur = defaultdict(list)
    for row in presence_rows:
        if not row.session:
            continue
        sec = (row.session.secteur or "Sans secteur").strip()
        sessions_by_secteur[sec].append(row.session)

    notes_q = PasseportNote.query.filter_by(participant_id=participant_id)
    files_q = PasseportPieceJointe.query.filter_by(participant_id=participant_id)
    if selected_secteur:
        notes_q = notes_q.filter(PasseportNote.secteur == selected_secteur)
        files_q = files_q.filter(PasseportPieceJointe.secteur == selected_secteur)

    if selected_categorie:
        notes_q = notes_q.filter(PasseportNote.categorie == selected_categorie)

    notes_total = notes_q.count()
    files_total = files_q.count()

    notes = (
        notes_q.order_by(PasseportNote.created_at.desc())
        .offset((notes_page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    files = (
        files_q.order_by(PasseportPieceJointe.created_at.desc())
        .offset((files_page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    all_notes = PasseportNote.query.filter_by(participant_id=participant_id).all()
    sectors = sorted(
        {(e.session.secteur if e.session else "") for e in events if e.session and e.session.secteur}
        | {(n.secteur or "") for n in all_notes if n.secteur}
        | set(sessions_by_secteur.keys())
    )
    if not sectors:
        sectors = ["Global"]

    notes_scope = [n for n in all_notes if (n.secteur or "") == selected_secteur] if selected_secteur else all_notes
    notes_by_cat = defaultdict(int)
    for n in notes_scope:
        notes_by_cat[_normalize_note_category(n.categorie)] += 1

    comp_ids = set(current_levels.keys())
    comp_rows = Competence.query.filter(Competence.id.in_(comp_ids)).all() if comp_ids else []
    comp_map = {c.id: c for c in comp_rows}

    referentiel_stats = defaultdict(lambda: {"total": 0, "valides": 0})
    for cid, lvl in current_levels.items():
        comp = comp_map.get(cid)
        if not comp:
            continue
        ref_name = comp.referentiel.nom if comp.referentiel else "Sans référentiel"
        referentiel_stats[ref_name]["total"] += 1
        if lvl >= 2:
            referentiel_stats[ref_name]["valides"] += 1

    summary = {}
    for sec in sectors:
        sec_sessions = sessions_by_secteur.get(sec, []) if sec != "Global" else [s for values in sessions_by_secteur.values() for s in values]
        sec_notes = [n for n in all_notes if (n.secteur or "") == sec] if sec != "Global" else all_notes
        sec_events = [e for e in events if e.session and e.session.secteur == sec] if sec != "Global" else events
        last_date = None
        for sess in sec_sessions:
            d = sess.date_session or sess.rdv_date
            if d and (last_date is None or d > last_date):
                last_date = d
        validated = len({e.competence_id for e in sec_events if e.etat >= 2})

        # --- Intensité réelle basée sur les dates ---
        dates = []
        for sess in sec_sessions:
            d = sess.date_session or sess.rdv_date
            if d:
                dates.append(d)

        if len(dates) >= 2:
            first_date = min(dates)
            last_date = max(dates)
            delta_days = (last_date - first_date).days
            months = max(delta_days / 30.0, 1)  # évite division par zéro
            intensity = round(len(dates) / months, 1)
        elif len(dates) == 1:
            intensity = 1.0
        else:
            intensity = 0.0
        # validated = len({e.competence_id for e in sec_events if e.etat >= 2})
        # intensity = round((len(sec_sessions) / 90.0) * 30.0, 1)
        summary[sec] = {
            "nb_sessions": len(sec_sessions),
            "last_participation": last_date,
            "nb_notes": len(sec_notes),
            "nb_competences_validees": validated,
            "intensity": intensity,
        }

    # --- Evaluation rapide (hors session ou rattachée) ---
    all_competences = (
        Competence.query.join(Referentiel, Competence.referentiel_id == Referentiel.id, isouter=True)
        .order_by(Referentiel.nom.asc().nullslast(), Competence.code.asc())
        .all()
    )

    session_choices = []
    seen = set()
    for row in presence_rows:
        if not row.session:
            continue
        if row.session.id in seen:
            continue
        seen.add(row.session.id)
        session_choices.append(row.session)

    session_choices = sorted(
        session_choices,
        key=lambda ss: (ss.date_session or ss.rdv_date or ss.created_at or datetime.datetime.min),
        reverse=True,
    )[:60]

    return render_template(
        "pedagogie/participant_passeport.html",
        participant=participant,
        events=events,
        current_levels=current_levels,
        summary=summary,
        sectors=sectors,
        selected_secteur=selected_secteur,
        notes=notes,
        notes_by_cat=notes_by_cat,
        selected_categorie=selected_categorie,
        note_categories=PASSPORT_NOTE_CATEGORIES,
        files=files,
        notes_page=notes_page,
        files_page=files_page,
        notes_total=notes_total,
        files_total=files_total,
        page_size=page_size,
        all_competences=all_competences,
        session_choices=session_choices,
        comp_map=comp_map,
        referentiel_stats=referentiel_stats,
    )


@bp.route("/participant/<int:participant_id>/passeport/evaluation", methods=["POST"])
@login_required
@require_perm("pedagogie:view")
def participant_passeport_evaluation(participant_id: int):
    """Créer / mettre à jour une évaluation (hors session ou rattachée)."""
    comp_id = request.form.get("competence_id", type=int)
    secteur = (request.form.get("secteur") or "").strip() or ""

    if not comp_id:
        flash("Compétence manquante.", "danger")
        return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id, secteur=secteur))

    try:
        etat = int(request.form.get("etat", 0))
    except Exception:
        etat = 0
    if etat not in (0, 1, 2, 3):
        etat = 0

    commentaire = (request.form.get("commentaire") or "").strip() or None

    session_id = request.form.get("session_id", type=int)
    if session_id == 0:
        session_id = None

    q = (
        Evaluation.query
        .filter_by(participant_id=participant_id, competence_id=comp_id, session_id=session_id)
        .order_by(Evaluation.date_evaluation.desc(), Evaluation.id.desc())
    )

    existing = q.first()
    dupes = q.offset(1).all()
    for d in dupes:
        db.session.delete(d)


    eval_date = datetime.date.today()
    if existing:
        existing.etat = etat
        existing.commentaire = commentaire
        existing.user_id = current_user.id
        existing.date_evaluation = eval_date
    else:
        ev = Evaluation(
            participant_id=participant_id,
            competence_id=comp_id,
            session_id=session_id,
            user_id=current_user.id,
            etat=etat,
            date_evaluation=eval_date,
            commentaire=commentaire,
        )
        db.session.add(ev)

    db.session.commit()
    flash("Évaluation enregistrée.", "success")
    return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id, secteur=secteur))


# ============================================================
# Passeport : Notes
# ============================================================

@bp.route("/participant/<int:participant_id>/passeport/note", methods=["POST"])
@login_required
@require_perm("pedagogie:view")
def participant_passeport_note(participant_id: int):
    contenu = (request.form.get("contenu") or "").strip()
    if not contenu:
        flash("Le texte de la note est obligatoire.", "danger")
        return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id))

    note = PasseportNote(
        participant_id=participant_id,
        session_id=request.form.get("session_id", type=int),
        secteur=(request.form.get("secteur") or "").strip() or None,
        categorie=_normalize_note_category(request.form.get("categorie")),
        contenu=contenu,
        created_by=current_user.id,
    )
    db.session.add(note)
    db.session.commit()
    flash("Note passeport enregistrée.", "success")
    return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id, secteur=note.secteur or ""))


@bp.route("/participant/<int:participant_id>/passeport/note/<int:note_id>/update", methods=["POST"])
@login_required
@require_perm("pedagogie:view")
def participant_passeport_note_update(participant_id: int, note_id: int):
    note = PasseportNote.query.get_or_404(note_id)
    if note.participant_id != participant_id:
        flash("Note invalide.", "danger")
        return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id))

    contenu = (request.form.get("contenu") or "").strip()
    if not contenu:
        flash("Le texte de la note est obligatoire.", "danger")
        return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id, secteur=note.secteur or ""))

    note.contenu = contenu
    note.categorie = _normalize_note_category(request.form.get("categorie"))
    db.session.commit()

    flash("Note mise à jour.", "success")
    return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id, secteur=note.secteur or ""))


@bp.route("/participant/<int:participant_id>/passeport/note/<int:note_id>/delete", methods=["POST"])
@login_required
@require_perm("pedagogie:view")
def participant_passeport_note_delete(participant_id: int, note_id: int):
    note = PasseportNote.query.get_or_404(note_id)
    if note.participant_id != participant_id:
        flash("Note invalide.", "danger")
        return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id))

    secteur = note.secteur or ""
    db.session.delete(note)
    db.session.commit()

    flash("Note supprimée.", "success")
    return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id, secteur=secteur))


# ============================================================
# Passeport : Pièces jointes (Upload / Download)
# ============================================================

@bp.route("/participant/<int:participant_id>/passeport/upload", methods=["POST"], endpoint="participant_passeport_file_upload")
@login_required
@require_perm("pedagogie:view")
def participant_passeport_file_upload(participant_id: int):
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Fichier manquant.", "danger")
        return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id))

    filename = secure_filename(f.filename)
    if not filename:
        flash("Nom de fichier invalide.", "danger")
        return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id))

    root = os.path.join(current_app.instance_path, "passeport_uploads", str(participant_id))
    os.makedirs(root, exist_ok=True)

    save_name = f"{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{filename}"
    path = os.path.join(root, save_name)
    f.save(path)

    row = PasseportPieceJointe(
        participant_id=participant_id,
        session_id=request.form.get("session_id", type=int),
        secteur=(request.form.get("secteur") or "").strip() or None,
        categorie=(request.form.get("categorie") or "atelier").strip() or "atelier",
        titre=(request.form.get("titre") or "").strip() or None,
        file_path=path,
        original_name=filename,
        mime_type=f.mimetype or mimetypes.guess_type(filename)[0],
        created_by=current_user.id,
    )
    db.session.add(row)
    db.session.commit()
    flash("Pièce jointe ajoutée.", "success")
    return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id, secteur=row.secteur or ""))


@bp.route("/participant/<int:participant_id>/passeport/file/<int:file_id>")
@login_required
@require_perm("pedagogie:view")
def participant_passeport_file_download(participant_id: int, file_id: int):
    row = PasseportPieceJointe.query.get_or_404(file_id)
    if row.participant_id != participant_id:
        flash("Pièce jointe invalide.", "danger")
        return redirect(url_for("pedagogie.participant_passeport", participant_id=participant_id))
    return send_file(row.file_path, as_attachment=True, download_name=row.original_name)


# ============================================================
# Pilotage / Export
# ============================================================

@bp.route("/pilotage")
@login_required
@require_perm("pedagogie:view")
def pilotage_objectifs():
    projet_id = request.args.get("projet_id", type=int)
    start_date = request.args.get("start_date") or None
    end_date = request.args.get("end_date") or None

    start = datetime.datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
    end = datetime.datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None

    rows = compute_objectif_scores(projet_id=projet_id, start_date=start, end_date=end)
    projets = Projet.query.order_by(Projet.nom.asc()).all()
    return render_template(
        "pedagogie/pilotage.html",
        rows=rows,
        projets=projets,
        projet_id=projet_id,
        start_date=start_date,
        end_date=end_date,
    )


@bp.route("/export_ra.csv")
@login_required
@require_perm("pedagogie:view")
def export_ra_csv():
    projet_id = request.args.get("projet_id", type=int)
    start_date = request.args.get("start_date") or None
    end_date = request.args.get("end_date") or None

    start = datetime.datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
    end = datetime.datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None

    rows = compute_objectif_scores(projet_id=projet_id, start_date=start, end_date=end)

    sio = StringIO()
    w = csv.writer(sio)
    w.writerow(["Projet", "Type objectif", "Objectif", "Score atteinte %", "Nb évaluations", "Nb participants", "Progression moyenne"])
    for r in rows:
        obj = r["objectif"]
        w.writerow(
            [
                obj.projet.nom if obj.projet else "",
                obj.type,
                obj.titre,
                "" if r["score"] is None else r["score"],
                r["evaluations"],
                r["participants"],
                "" if r["progression_moyenne"] is None else r["progression_moyenne"],
            ]
        )

    return Response(
        sio.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=rapport_activite_pedagogie.csv"},
    )