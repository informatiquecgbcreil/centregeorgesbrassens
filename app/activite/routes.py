import os
import base64
import secrets
from datetime import datetime, date

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    current_app,
    send_file,
    abort,
)
from werkzeug.utils import secure_filename
from flask_login import login_required, current_user
from sqlalchemy import or_

from app.extensions import db
from app.models import (
    AtelierActivite,
    SessionActivite,
    Participant,
    PresenceActivite,
    Quartier,
    Referentiel,
    Competence,
    AtelierCapaciteMois,
    ArchiveEmargement,
    Evaluation,
    Objectif,
    PedagogieModule,
    PlanProjetAtelierModule,
    Projet,
    PasseportNote,
    SessionScheduleEditLog,
    Framework,
    Skill,
    SessionSkill,
)

from ..rbac import require_perm
from . import bp
from .services.docx_utils import (
    generate_collectif_docx_pdf,
    generate_individuel_mensuel_docx,
    finalize_individuel_mensuel_pdf,
)
from .services.mail_utils import send_email_with_attachment
from app.services.quartiers import normalize_quartier_for_ville
from app.utils.delete_guard import commit_delete


PASSPORT_NOTE_CATEGORIES = {"journal", "participation", "progression", "temoignage", "session"}


def _require_any_perm(*codes: str) -> None:
    if not current_user.is_authenticated:
        abort(403)
    if not any(current_user.has_perm(code) for code in codes):
        abort(403)


def _normalize_note_category(raw: str | None) -> str:
    val = (raw or "session").strip().lower()
    return val if val in PASSPORT_NOTE_CATEGORIES else "session"


# ------------------ Helpers ------------------


def _is_admin_global() -> bool:
    return current_user.is_authenticated and getattr(current_user, "has_role", lambda *_: False)("admin_tech")


def _user_secteur() -> str:
    if _is_admin_global():
        return (request.args.get("secteur") or current_user.secteur_assigne or "").strip() or "Numérique"
    # responsable_secteur = admin de son secteur
    return (current_user.secteur_assigne or "").strip() or "Numérique"


def _load_referentiels():
    return Referentiel.query.order_by(Referentiel.nom.asc()).all()


def _ensure_seed_ateliers(secteur: str) -> None:
    """Seed minimal ateliers for a smoother IRL start.

    We keep this extremely conservative to avoid surprising colleagues.
    - Numérique: two INDIVIDUEL_MENSUEL ateliers requested by Antoine
    """
    if not secteur:
        return
    if secteur.strip().lower() not in {"numérique", "numerique"}:
        return

    # If the sector already has ateliers, we don't seed anything.
    if AtelierActivite.query.filter_by(secteur=secteur, is_deleted=False).count() > 0:
        return

    seeds = [
        ("S.O(rdi).S", "INDIVIDUEL_MENSUEL"),
        ("Accès aux droits", "INDIVIDUEL_MENSUEL"),
    ]
    for nom, type_atelier in seeds:
        db.session.add(
            AtelierActivite(
                secteur=secteur,
                nom=nom,
                type_atelier=type_atelier,
                # heures_dispo_defaut_mois left to the sector referent
                heures_dispo_defaut_mois=None,
                # Provide a sensible default motifs list (editable)
                motifs_json=None,
            )
        )
    db.session.commit()


def _safe_unlink(path: str | None) -> None:
    """Supprime un fichier si possible, sans jamais faire planter la requête."""
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _sort_competences(items):
    return sorted(items, key=lambda c: ((c.code or "").lower(), (c.nom or "").lower()))


def _collect_session_competences(session: SessionActivite, session_objectifs: list[Objectif] | None = None):
    """Retourne les compétences de session via 3 sources fusionnées:

    1) compétences explicitement liées à la session,
    2) compétences des modules liés à la session,
    3) compétences des objectifs opérationnels de session.

    Cette fusion évite le bug où l'évaluation affiche "Aucune compétence"
    alors que des modules ont bien été sélectionnés.
    """
    by_id = {}

    for comp in (getattr(session, "competences", []) or []):
        by_id[comp.id] = comp

    for module in (getattr(session, "modules", []) or []):
        for comp in (getattr(module, "competences", []) or []):
            by_id[comp.id] = comp

    objectifs = session_objectifs
    if objectifs is None:
        objectifs = Objectif.query.filter_by(session_id=session.id, type="operationnel").all()

    for obj in objectifs:
        for comp in (getattr(obj, "competences", []) or []):
            by_id[comp.id] = comp

    return _sort_competences(list(by_id.values()))


def _ensure_month_capacity(atelier: AtelierActivite, session: SessionActivite) -> None:
    """Crée la capacité mensuelle si elle n'existe pas (ateliers INDIVIDUEL_MENSUEL)."""
    if atelier.type_atelier != "INDIVIDUEL_MENSUEL":
        return
    if not session.rdv_date:
        return
    annee, mois = session.rdv_date.year, session.rdv_date.month
    cap = AtelierCapaciteMois.query.filter_by(atelier_id=atelier.id, annee=annee, mois=mois).first()
    if cap:
        return
    heures = float(atelier.heures_dispo_defaut_mois or 0.0)
    cap = AtelierCapaciteMois(atelier_id=atelier.id, annee=annee, mois=mois, heures_dispo=heures, locked=False)
    db.session.add(cap)
    db.session.commit()


def _best_archive_path(arch: ArchiveEmargement, kind: str) -> str | None:
    """Return the best available file path for download/email.

    kind: 'docx' or 'pdf'
    Prefers corrected version if present.
    """
    if not arch:
        return None
    if kind == "pdf":
        return arch.corrected_pdf_path or arch.pdf_path
    return arch.corrected_docx_path or arch.docx_path


# ------------------ Home (ateliers) ------------------


@bp.route("/")
@login_required
def index():
    _require_any_perm("ateliers:view", "emargement:view")
    secteur = _user_secteur()
    _ensure_seed_ateliers(secteur)
    corbeille = (request.args.get("corbeille") == "1")
    show_inactive = (request.args.get("inactifs") == "1")
    q = AtelierActivite.query.filter_by(secteur=secteur)
    if corbeille:
        q = q.filter(AtelierActivite.is_deleted.is_(True))
    else:
        q = q.filter(AtelierActivite.is_deleted.is_(False))
        if not show_inactive:
            q = q.filter(AtelierActivite.is_active.is_(True))
    ateliers = q.order_by(AtelierActivite.nom.asc()).all()
    return render_template(
        "activite/index.html",
        secteur=secteur,
        ateliers=ateliers,
        is_admin_global=_is_admin_global(),
        corbeille=corbeille,
        show_inactive=show_inactive,
    )


# ------------------ Gestion Participants (par secteur) ------------------


@bp.route("/participants")
@login_required
def participants():
    _require_any_perm("participants:view", "participants:view_all")
    """Liste des participants ayant au moins une présence dans le secteur."""
    secteur = _user_secteur()
    q = (request.args.get("q") or "").strip()

    base = (
        db.session.query(Participant)
        .join(PresenceActivite, PresenceActivite.participant_id == Participant.id)
        .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
        .filter(SessionActivite.secteur == secteur)
        .distinct()
    )
    if q:
        like = f"%{q.lower()}%"
        base = base.filter(
            or_(
                db.func.lower(Participant.nom).like(like),
                db.func.lower(Participant.prenom).like(like),
                db.func.lower(db.func.coalesce(Participant.email, "")).like(like),
                db.func.lower(db.func.coalesce(Participant.telephone, "")).like(like),
            )
        )

    participants_list = base.order_by(Participant.nom.asc(), Participant.prenom.asc()).limit(500).all()

    stats_map = {}
    rows = (
        db.session.query(
            PresenceActivite.participant_id,
            db.func.count(PresenceActivite.id).label("visites"),
            db.func.max(PresenceActivite.created_at).label("last_seen"),
        )
        .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
        .filter(SessionActivite.secteur == secteur)
        .group_by(PresenceActivite.participant_id)
        .all()
    )
    for r in rows:
        stats_map[r.participant_id] = {"visites": int(r.visites or 0), "last_seen": r.last_seen}

    return render_template(
        "activite/participants.html",
        secteur=secteur,
        q=q,
        participants=participants_list,
        stats_map=stats_map,
        is_admin_global=_is_admin_global(),
    )


@bp.route("/participant/<int:participant_id>/edit", methods=["GET", "POST"])
@login_required
def participant_edit(participant_id: int):
    require_perm("participants:edit")(lambda: None)()
    secteur = _user_secteur()
    p = Participant.query.get_or_404(participant_id)

    if not _is_admin_global():
        in_secteur = (
            db.session.query(PresenceActivite.id)
            .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
            .filter(PresenceActivite.participant_id == p.id)
            .filter(SessionActivite.secteur == secteur)
            .first()
            is not None
        )
        if not in_secteur:
            flash("Accès refusé.", "danger")
            return redirect(url_for("activite.participants"))

    if request.method == "POST":
        p.nom = (request.form.get("nom") or p.nom).strip()
        p.prenom = (request.form.get("prenom") or p.prenom).strip()
        p.adresse = (request.form.get("adresse") or "").strip() or None
        p.ville = (request.form.get("ville") or "").strip() or None
        p.email = (request.form.get("email") or "").strip() or None
        p.telephone = (request.form.get("telephone") or "").strip() or None
        p.genre = (request.form.get("genre") or "").strip() or None
        p.type_public = (request.form.get("type_public") or p.type_public or "H").strip()[:2]

        dn = (request.form.get("date_naissance") or "").strip()
        if dn:
            try:
                p.date_naissance = datetime.strptime(dn, "%Y-%m-%d").date()
            except Exception:
                flash("Date de naissance invalide.", "warning")
        else:
            p.date_naissance = None

        qid_raw = (request.form.get("quartier_id") or "").strip()
        p.quartier_id = normalize_quartier_for_ville(p.ville, qid_raw)

        db.session.commit()
        flash("Le participant a bien été mis à jour.", "success")
        return redirect(url_for("activite.participants"))

    quartiers = Quartier.query.order_by(Quartier.ville.asc(), Quartier.nom.asc()).all()
    return render_template("activite/participant_form.html", secteur=secteur, p=p, quartiers=quartiers)


@bp.route("/participant/<int:participant_id>/anonymize", methods=["POST"])
@login_required
def participant_anonymize(participant_id: int):
    require_perm("participants:anonymize")(lambda: None)()
    """Anonymise un participant (conserve les stats mais supprime les identifiants)."""
    secteur = _user_secteur()
    p = Participant.query.get_or_404(participant_id)

    if not _is_admin_global():
        in_secteur = (
            db.session.query(PresenceActivite.id)
            .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
            .filter(PresenceActivite.participant_id == p.id)
            .filter(SessionActivite.secteur == secteur)
            .first()
            is not None
        )
        if not in_secteur:
            flash("Accès refusé.", "danger")
            return redirect(url_for("activite.participants"))

    p.nom = "Anonyme"
    p.prenom = "Anonyme"
    p.adresse = None
    p.ville = None
    p.email = None
    p.telephone = None

    strict = (request.form.get("strict") == "1")
    if strict:
        p.genre = None
        p.date_naissance = None
        p.type_public = "H"
        p.quartier_id = None

    db.session.commit()
    flash("Le participant a bien été anonymisé.", "success")
    return redirect(url_for("activite.participants"))


@bp.route("/participant/<int:participant_id>/delete", methods=["POST"])
@login_required
def participant_delete(participant_id: int):
    require_perm("participants:delete")(lambda: None)()
    """Suppression définitive : uniquement si le participant n'existe pas dans d'autres secteurs.

    (Admin global : bypass.)
    """
    secteur = _user_secteur()
    p = Participant.query.get_or_404(participant_id)

    if not _is_admin_global():
        other = (
            db.session.query(PresenceActivite.id)
            .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
            .filter(PresenceActivite.participant_id == p.id)
            .filter(SessionActivite.secteur != secteur)
            .first()
        )
        if other is not None:
            flash("La suppression est refusée : ce participant est utilisé dans d'autres secteurs. Utilisez l'anonymisation à la place.", "warning")
            return redirect(url_for("activite.participants"))

    presences = PresenceActivite.query.filter_by(participant_id=p.id).all()
    signature_paths = [pr.signature_path for pr in presences if pr.signature_path]
    for pr in presences:
        db.session.delete(pr)

    db.session.delete(p)
    if commit_delete(
        f"le participant « {p.nom_complet()} »",
        "Participant supprimé définitivement.",
        blocked_message=f"Impossible de supprimer le participant « {p.nom_complet()} » : il est encore référencé ailleurs. Utilisez plutôt l'anonymisation si vous souhaitez conserver l'historique.",
    ):
        for rel in signature_paths:
            _safe_unlink(rel)
    return redirect(url_for("activite.participants"))


# ------------------ Gestion Ateliers ------------------


@bp.route("/atelier/new", methods=["GET", "POST"])
@login_required
def atelier_new():
    require_perm("ateliers:edit")(lambda: None)()
    secteur = _user_secteur()
    if request.method == "POST":
        nom = (request.form.get("nom") or "").strip()
        if not nom:
            flash("Le nom de l'activité est obligatoire.", "danger")
            referentiels = _load_referentiels()
            return render_template(
                "activite/atelier_form.html",
                secteur=secteur,
                atelier=None,
                referentiels=referentiels,
                selected_competences=set(),
            )

        type_atelier = request.form.get("type_atelier") or "COLLECTIF"
        description = (request.form.get("description") or "").strip() or None
        duree_defaut_minutes = request.form.get("duree_defaut_minutes") or None

        capacite_defaut = request.form.get("capacite_defaut") or None
        heures_dispo_defaut_mois = request.form.get("heures_dispo_defaut_mois") or None

        motifs = [m.strip() for m in (request.form.get("motifs") or "").split(";") if m.strip()]
        is_active = request.form.get("is_active") in {"1", "true", "on", "yes", "YES"}
        continuity_parent_id = request.form.get("continuity_parent_id", type=int)
        motifs_json = None
        if motifs:
            import json as _json
            motifs_json = _json.dumps(motifs, ensure_ascii=False)

        a = AtelierActivite(
            secteur=secteur,
            nom=nom,
            description=description,
            type_atelier=type_atelier,
            capacite_defaut=int(capacite_defaut) if capacite_defaut else None,
            heures_dispo_defaut_mois=float(heures_dispo_defaut_mois) if heures_dispo_defaut_mois else None,
            duree_defaut_minutes=int(duree_defaut_minutes) if duree_defaut_minutes else None,
            motifs_json=motifs_json,
            is_active=is_active,
            continuity_parent_id=continuity_parent_id,
        )
        competence_ids = [int(cid) for cid in request.form.getlist("competence_ids") if cid.isdigit()]
        if competence_ids:
            a.competences = Competence.query.filter(Competence.id.in_(competence_ids)).all()
        db.session.add(a)
        db.session.commit()
        flash("L'activité a bien été créée.", "success")
        return redirect(url_for("activite.index"))

    referentiels = _load_referentiels()
    ateliers_continuite = AtelierActivite.query.filter(AtelierActivite.secteur == secteur, AtelierActivite.is_deleted.is_(False)).order_by(AtelierActivite.nom.asc()).all()
    return render_template(
        "activite/atelier_form.html",
        secteur=secteur,
        atelier=None,
        referentiels=referentiels,
        selected_competences=set(),
        ateliers_continuite=ateliers_continuite,
    )


@bp.route("/atelier/<int:atelier_id>/edit", methods=["GET", "POST"])
@login_required
def atelier_edit(atelier_id: int):
    require_perm("ateliers:edit")(lambda: None)()
    secteur = _user_secteur()
    atelier = AtelierActivite.query.get_or_404(atelier_id)
    if atelier.is_deleted:
        flash("Cet atelier est dans la corbeille. Restaure-le pour le modifier.", "warning")
        return redirect(url_for("activite.index", corbeille=1))
    if not _is_admin_global() and atelier.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    if request.method == "POST":
        atelier.nom = (request.form.get("nom") or atelier.nom).strip()
        atelier.description = (request.form.get("description") or "").strip() or None
        atelier.type_atelier = request.form.get("type_atelier") or atelier.type_atelier

        duree_defaut_minutes = request.form.get("duree_defaut_minutes") or None
        atelier.duree_defaut_minutes = int(duree_defaut_minutes) if duree_defaut_minutes else None

        capacite_defaut = request.form.get("capacite_defaut") or None
        atelier.capacite_defaut = int(capacite_defaut) if capacite_defaut else None

        heures_dispo_defaut_mois = request.form.get("heures_dispo_defaut_mois") or None
        atelier.heures_dispo_defaut_mois = float(heures_dispo_defaut_mois) if heures_dispo_defaut_mois else None

        motifs = [m.strip() for m in (request.form.get("motifs") or "").split(";") if m.strip()]
        is_active = request.form.get("is_active") in {"1", "true", "on", "yes", "YES"}
        continuity_parent_id = request.form.get("continuity_parent_id", type=int)
        if continuity_parent_id == atelier.id:
            continuity_parent_id = None
        atelier.is_active = is_active
        atelier.continuity_parent_id = continuity_parent_id
        if motifs:
            import json as _json
            atelier.motifs_json = _json.dumps(motifs, ensure_ascii=False)
        else:
            atelier.motifs_json = None

        competence_ids = [int(cid) for cid in request.form.getlist("competence_ids") if cid.isdigit()]
        if competence_ids:
            atelier.competences = Competence.query.filter(Competence.id.in_(competence_ids)).all()
        else:
            atelier.competences = []

        db.session.commit()
        flash("L'activité a bien été mise à jour.", "success")
        return redirect(url_for("activite.index"))

    motifs_str = "; ".join(atelier.motifs() or [])
    referentiels = _load_referentiels()
    selected_competences = {c.id for c in atelier.competences}
    ateliers_continuite = AtelierActivite.query.filter(AtelierActivite.secteur == secteur, AtelierActivite.is_deleted.is_(False), AtelierActivite.id != atelier.id).order_by(AtelierActivite.nom.asc()).all()
    return render_template(
        "activite/atelier_form.html",
        secteur=secteur,
        atelier=atelier,
        motifs_str=motifs_str,
        referentiels=referentiels,
        selected_competences=selected_competences,
        ateliers_continuite=ateliers_continuite,
    )


@bp.route("/atelier/<int:atelier_id>/sessions")
@login_required
def sessions(atelier_id: int):
    _require_any_perm("ateliers:view", "emargement:view")
    secteur = _user_secteur()
    atelier = AtelierActivite.query.get_or_404(atelier_id)
    corbeille = (request.args.get("corbeille") == "1")
    if atelier.is_deleted and not corbeille:
        flash("Cet atelier est dans la corbeille.", "warning")
        return redirect(url_for("activite.index", corbeille=1))
    if not _is_admin_global() and atelier.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    q = SessionActivite.query.filter_by(atelier_id=atelier.id)
    if corbeille:
        q = q.filter(SessionActivite.is_deleted.is_(True))
    else:
        q = q.filter(SessionActivite.is_deleted.is_(False))
    q = q.order_by(SessionActivite.created_at.desc())
    sessions_list = q.limit(200).all()

    session_stats = []
    for s in sessions_list:
        nb = len(s.presences)
        cap = s.capacite or 0
        taux = None
        if s.session_type == "COLLECTIF" and cap > 0:
            taux = round((nb / cap) * 100, 1)
        session_stats.append((s, nb, cap, taux))

    return render_template(
        "activite/sessions.html",
        secteur=secteur,
        atelier=atelier,
        sessions=session_stats,
        corbeille=corbeille,
        current_year=date.today().year,
        current_month=date.today().month,
    )


# ------------------ Suppression / Restauration (soft-delete) ------------------


@bp.route("/atelier/<int:atelier_id>/delete", methods=["POST"])
@login_required
@require_perm("activite:delete")
def atelier_delete(atelier_id: int):
    require_perm("activite:delete")(lambda: None)()
    """Met un atelier (et ses sessions) en corbeille."""
    secteur = _user_secteur()
    atelier = AtelierActivite.query.get_or_404(atelier_id)
    if not _is_admin_global() and atelier.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    if atelier.is_deleted:
        flash("Atelier déjà dans la corbeille.", "info")
        return redirect(url_for("activite.index", corbeille=1))

    atelier.is_deleted = True
    atelier.deleted_at = datetime.utcnow()

    for s in SessionActivite.query.filter_by(atelier_id=atelier.id).all():
        s.is_deleted = True
        s.deleted_at = datetime.utcnow()
        s.kiosk_open = False
        s.kiosk_pin = None
        s.kiosk_token = None

    db.session.commit()
    flash("L'activité a été placée dans la corbeille. Elle peut être restaurée.", "success")
    return redirect(url_for("activite.index"))


@bp.route("/atelier/<int:atelier_id>/restore", methods=["POST"])
@login_required
def atelier_restore(atelier_id: int):
    require_perm("activite:restore")(lambda: None)()
    """Restaure un atelier (et ses sessions)."""
    secteur = _user_secteur()
    atelier = AtelierActivite.query.get_or_404(atelier_id)
    if not _is_admin_global() and atelier.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    if not atelier.is_deleted:
        flash("Atelier déjà actif.", "info")
        return redirect(url_for("activite.index"))

    atelier.is_deleted = False
    atelier.deleted_at = None
    for s in SessionActivite.query.filter_by(atelier_id=atelier.id).all():
        s.is_deleted = False
        s.deleted_at = None
    db.session.commit()
    flash("L'activité a bien été restaurée.", "success")
    return redirect(url_for("activite.index"))


@bp.route("/session/<int:session_id>/delete", methods=["POST"])
@login_required
@require_perm("activite:delete")
def session_delete(session_id: int):
    require_perm("activite:delete")(lambda: None)()
    """Met une session/RDV en corbeille."""
    secteur = _user_secteur()
    s = SessionActivite.query.get_or_404(session_id)
    atelier = AtelierActivite.query.get_or_404(s.atelier_id)
    if not _is_admin_global() and s.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    if s.is_deleted:
        flash("Session déjà dans la corbeille.", "info")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id, corbeille=1))

    s.is_deleted = True
    s.deleted_at = datetime.utcnow()
    s.kiosk_open = False
    s.kiosk_pin = None
    s.kiosk_token = None
    db.session.commit()

    flash("Session placée dans la corbeille (restaurable).", "success")
    return redirect(url_for("activite.sessions", atelier_id=atelier.id))


@bp.route("/session/<int:session_id>/restore", methods=["POST"])
@login_required
def session_restore(session_id: int):
    require_perm("activite:restore")(lambda: None)()
    """Restaure une session/RDV."""
    secteur = _user_secteur()
    s = SessionActivite.query.get_or_404(session_id)
    atelier = AtelierActivite.query.get_or_404(s.atelier_id)
    if not _is_admin_global() and s.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))
    if atelier.is_deleted:
        flash("Restaure d'abord l'atelier.", "warning")
        return redirect(url_for("activite.index", corbeille=1))

    if not s.is_deleted:
        flash("Session déjà active.", "info")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id))

    s.is_deleted = False
    s.deleted_at = None
    db.session.commit()
    flash("Session restaurée.", "success")
    return redirect(url_for("activite.sessions", atelier_id=atelier.id))


@bp.route("/session/<int:session_id>/purge", methods=["POST"])
@login_required
@require_perm("activite:purge")
def session_purge(session_id: int):
    require_perm("activite:purge")(lambda: None)()
    """Suppression définitive d'une session (nécessite qu'elle soit déjà en corbeille)."""
    secteur = _user_secteur()
    s = SessionActivite.query.get_or_404(session_id)
    atelier = AtelierActivite.query.get_or_404(s.atelier_id)

    if not _is_admin_global() and s.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    if not _is_admin_global() and not s.is_deleted:
        flash("Place d'abord la session dans la corbeille avant suppression définitive.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id))

    presences = PresenceActivite.query.filter_by(session_id=s.id).all()
    signature_paths = [pr.signature_path for pr in presences if pr.signature_path]
    for pr in presences:
        db.session.delete(pr)

    archives = ArchiveEmargement.query.filter_by(session_id=s.id).all()
    archive_paths = []
    for a in archives:
        archive_paths.extend([p for p in [a.docx_path, a.pdf_path, a.corrected_docx_path, a.corrected_pdf_path] if p])
        db.session.delete(a)

    db.session.delete(s)
    if commit_delete(
        f"la session du {s.date_session}",
        "Session supprimée définitivement.",
        blocked_message="Impossible de supprimer définitivement cette session : elle est encore référencée ailleurs dans l'application.",
    ):
        for rel in signature_paths + archive_paths:
            _safe_unlink(rel)
    return redirect(url_for("activite.sessions", atelier_id=atelier.id, corbeille=1))


# ------------------ Création Session ------------------


@bp.route("/atelier/<int:atelier_id>/session/new", methods=["GET", "POST"])
@login_required
def session_new(atelier_id: int):
    require_perm("ateliers:edit")(lambda: None)()
    secteur = _user_secteur()
    atelier = AtelierActivite.query.get_or_404(atelier_id)
    if atelier.is_deleted:
        flash("Cet atelier est dans la corbeille. Restaure-le pour créer une session.", "warning")
        return redirect(url_for("activite.index", corbeille=1))
    if not _is_admin_global() and atelier.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    if request.method == "POST":
        session_type = atelier.type_atelier
        if session_type == "INDIVIDUEL_MENSUEL":
            rdv_date = request.form.get("rdv_date")
            rdv_debut = (request.form.get("rdv_debut") or "").strip() or None
            rdv_fin = (request.form.get("rdv_fin") or "").strip() or None
            if not rdv_date:
                flash("Date RDV obligatoire.", "danger")
                modules = PedagogieModule.query.filter(PedagogieModule.actif.is_(True)).order_by(PedagogieModule.nom.asc()).all()
                return render_template(
                    "activite/session_form.html",
                    secteur=secteur,
                    atelier=atelier,
                    session=None,
                    modules=modules,
                    projets_atelier=[],
                    projet_id=None,
                )
            rdv_date_obj = datetime.strptime(rdv_date, "%Y-%m-%d").date()
            s = SessionActivite(
                atelier_id=atelier.id,
                secteur=atelier.secteur,
                session_type="INDIVIDUEL_MENSUEL",
                rdv_date=rdv_date_obj,
                rdv_debut=rdv_debut,
                rdv_fin=rdv_fin,
            )
        else:
            date_session = request.form.get("date_session")
            heure_debut = (request.form.get("heure_debut") or "").strip() or None
            heure_fin = (request.form.get("heure_fin") or "").strip() or None
            capacite = request.form.get("capacite") or atelier.capacite_defaut
            if not date_session:
                flash("Date de session obligatoire.", "danger")
                modules = PedagogieModule.query.filter(PedagogieModule.actif.is_(True)).order_by(PedagogieModule.nom.asc()).all()
                return render_template(
                    "activite/session_form.html",
                    secteur=secteur,
                    atelier=atelier,
                    session=None,
                    modules=modules,
                    projets_atelier=[],
                    projet_id=None,
                )
            date_obj = datetime.strptime(date_session, "%Y-%m-%d").date()
            s = SessionActivite(
                atelier_id=atelier.id,
                secteur=atelier.secteur,
                session_type="COLLECTIF",
                date_session=date_obj,
                heure_debut=heure_debut,
                heure_fin=heure_fin,
                capacite=int(capacite) if capacite else None,
            )

        module_ids = [int(mid) for mid in request.form.getlist("module_ids") if str(mid).isdigit()]
        modules = (
            PedagogieModule.query.filter(PedagogieModule.id.in_(module_ids), PedagogieModule.actif.is_(True)).all()
            if module_ids
            else []
        )

        competence_ids = set()
        for mod in modules:
            for comp in mod.competences:
                competence_ids.add(comp.id)

        s.competences = Competence.query.filter(Competence.id.in_(list(competence_ids))).all() if competence_ids else []
        s.modules = modules

        db.session.add(s)
        db.session.commit()
        flash("La session a bien été créée.", "success")
        return redirect(url_for("activite.emargement", session_id=s.id))

    projet_id = request.args.get("projet_id", type=int)
    projets_atelier = (
        Projet.query.join(PlanProjetAtelierModule, PlanProjetAtelierModule.projet_id == Projet.id)
        .filter(PlanProjetAtelierModule.atelier_id == atelier.id)
        .distinct()
        .order_by(Projet.nom.asc())
        .all()
    )

    if projet_id:
        modules = (
            PedagogieModule.query.join(PlanProjetAtelierModule, PlanProjetAtelierModule.module_id == PedagogieModule.id)
            .filter(
                PlanProjetAtelierModule.atelier_id == atelier.id,
                PlanProjetAtelierModule.projet_id == projet_id,
                PedagogieModule.actif.is_(True),
            )
            .order_by(PedagogieModule.nom.asc())
            .all()
        )
    else:
        modules = (
            PedagogieModule.query.join(PlanProjetAtelierModule, PlanProjetAtelierModule.module_id == PedagogieModule.id)
            .filter(
                PlanProjetAtelierModule.atelier_id == atelier.id,
                PedagogieModule.actif.is_(True),
            )
            .distinct()
            .order_by(PedagogieModule.nom.asc())
            .all()
        )

    return render_template(
        "activite/session_form.html",
        secteur=secteur,
        atelier=atelier,
        session=None,
        modules=modules,
        projets_atelier=projets_atelier,
        projet_id=projet_id,
    )


@bp.route("/session/<int:session_id>/edit-schedule", methods=["GET", "POST"])
@login_required
def session_edit_schedule(session_id: int):
    require_perm("ateliers:edit")(lambda: None)()
    """Autorise la correction de date/heure/capacité d'une session déjà émargée, avec traçabilité."""
    secteur = _user_secteur()
    s = SessionActivite.query.get_or_404(session_id)
    atelier = AtelierActivite.query.get_or_404(s.atelier_id)

    if s.is_deleted or atelier.is_deleted:
        flash("Cette session/atelier est dans la corbeille.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id, corbeille=1))
    if not _is_admin_global() and s.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    if request.method == "POST":
        reason = (request.form.get("edit_reason") or "").strip()
        if len(reason) < 8:
            flash("Merci de préciser une raison (au moins 8 caractères) pour la traçabilité.", "danger")
            return redirect(url_for("activite.session_edit_schedule", session_id=s.id))

        old_date = s.rdv_date if s.session_type == "INDIVIDUEL_MENSUEL" else s.date_session
        old_start = s.rdv_debut if s.session_type == "INDIVIDUEL_MENSUEL" else s.heure_debut
        old_end = s.rdv_fin if s.session_type == "INDIVIDUEL_MENSUEL" else s.heure_fin

        if s.session_type == "INDIVIDUEL_MENSUEL":
            rdv_date = request.form.get("rdv_date")
            if not rdv_date:
                flash("Date RDV obligatoire.", "danger")
                return redirect(url_for("activite.session_edit_schedule", session_id=s.id))
            s.rdv_date = datetime.strptime(rdv_date, "%Y-%m-%d").date()
            s.rdv_debut = (request.form.get("rdv_debut") or "").strip() or None
            s.rdv_fin = (request.form.get("rdv_fin") or "").strip() or None
        else:
            date_session = request.form.get("date_session")
            if not date_session:
                flash("Date de session obligatoire.", "danger")
                return redirect(url_for("activite.session_edit_schedule", session_id=s.id))
            s.date_session = datetime.strptime(date_session, "%Y-%m-%d").date()
            s.heure_debut = (request.form.get("heure_debut") or "").strip() or None
            s.heure_fin = (request.form.get("heure_fin") or "").strip() or None
            capacite = request.form.get("capacite")
            s.capacite = int(capacite) if capacite else None

        new_date = s.rdv_date if s.session_type == "INDIVIDUEL_MENSUEL" else s.date_session
        new_start = s.rdv_debut if s.session_type == "INDIVIDUEL_MENSUEL" else s.heure_debut
        new_end = s.rdv_fin if s.session_type == "INDIVIDUEL_MENSUEL" else s.heure_fin

        if old_date == new_date and old_start == new_start and old_end == new_end:
            flash("Aucun changement détecté sur date/heure.", "info")
            return redirect(url_for("activite.session_edit_schedule", session_id=s.id))

        log = SessionScheduleEditLog(
            session_id=s.id,
            atelier_id=atelier.id,
            secteur=s.secteur,
            old_date=old_date,
            old_start=old_start,
            old_end=old_end,
            new_date=new_date,
            new_start=new_start,
            new_end=new_end,
            reason=reason,
            edited_by=getattr(current_user, "id", None),
        )
        db.session.add(log)
        db.session.commit()
        flash("Date/heure de session mises à jour et tracées.", "success")
        return redirect(url_for("activite.emargement", session_id=s.id))

    edits = (
        SessionScheduleEditLog.query.filter_by(session_id=s.id)
        .order_by(SessionScheduleEditLog.edited_at.desc())
        .limit(30)
        .all()
    )
    return render_template(
        "activite/session_edit_schedule.html",
        secteur=secteur,
        atelier=atelier,
        session=s,
        edits=edits,
    )


# ------------------ Compétences (tag au niveau SESSION) ------------------


@bp.route("/session/<int:session_id>/skills", methods=["GET"])
@login_required
def session_skills(session_id: int):
    _require_any_perm("pedagogie:view", "pedagogie:edit")
    """Associer des compétences legacy (Referentiel/Competence) à une session.

    Important : cette page doit refléter exactement ce que l'émargement lit,
    donc on se base sur SessionActivite.competences + SessionActivite.modules.
    """
    secteur = _user_secteur()
    s = SessionActivite.query.get_or_404(session_id)
    atelier = AtelierActivite.query.get_or_404(s.atelier_id)

    if s.is_deleted or atelier.is_deleted:
        flash("Cette session/atelier est dans la corbeille.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id, corbeille=1))
    if not _is_admin_global() and s.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    referentiel_id = request.args.get("referentiel_id", type=int)
    q = (request.args.get("q") or "").strip()

    referentiels = Referentiel.query.order_by(Referentiel.nom.asc()).all()
    ref_map = {ref.id: ref for ref in referentiels}
    if referentiel_id is None and referentiels:
        referentiel_id = referentiels[0].id

    current_direct = _sort_competences(list(getattr(s, "competences", []) or []))
    current_direct_ids = {c.id for c in current_direct}

    inherited_from_modules = []
    inherited_ids = set()
    for module in (getattr(s, "modules", []) or []):
        for comp in (getattr(module, "competences", []) or []):
            if comp.id not in inherited_ids:
                inherited_ids.add(comp.id)
                inherited_from_modules.append(comp)
    inherited_from_modules = _sort_competences(inherited_from_modules)

    current_merged = _collect_session_competences(s)
    current_merged_ids = {c.id for c in current_merged}

    module_source_map = {}
    for module in (getattr(s, "modules", []) or []):
        for comp in (getattr(module, "competences", []) or []):
            module_source_map.setdefault(comp.id, []).append(module.nom)
    for comp_id in list(module_source_map.keys()):
        module_source_map[comp_id] = sorted(set(module_source_map[comp_id]), key=lambda x: (x or '').lower())

    results = []
    if q:
        cq = Competence.query
        if referentiel_id:
            cq = cq.filter(Competence.referentiel_id == referentiel_id)
        like = f"%{q}%"
        cq = cq.filter((Competence.code.ilike(like)) | (Competence.nom.ilike(like)) | (Competence.description.ilike(like)))
        results = cq.order_by(Competence.code.asc(), Competence.nom.asc()).limit(80).all()

    return render_template(
        "activite/session_skills.html",
        secteur=secteur,
        atelier=atelier,
        session=s,
        referentiels=referentiels,
        ref_map=ref_map,
        referentiel_id=referentiel_id,
        q=q,
        current_direct=current_direct,
        current_direct_ids=current_direct_ids,
        inherited_from_modules=inherited_from_modules,
        inherited_ids=inherited_ids,
        current_merged=current_merged,
        current_merged_ids=current_merged_ids,
        module_source_map=module_source_map,
        results=results,
    )


@bp.route("/session/<int:session_id>/skills/add", methods=["POST"])
@login_required
def session_skill_add(session_id: int):
    secteur = _user_secteur()
    s = SessionActivite.query.get_or_404(session_id)
    atelier = AtelierActivite.query.get_or_404(s.atelier_id)

    if s.is_deleted or atelier.is_deleted:
        flash("Cette session/atelier est dans la corbeille.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id, corbeille=1))
    if not _is_admin_global() and s.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    competence_id = request.form.get("competence_id", type=int)
    if not competence_id:
        flash("Compétence manquante.", "warning")
        return redirect(url_for("activite.session_skills", session_id=s.id))

    comp = Competence.query.get_or_404(competence_id)
    existing_ids = {c.id for c in (getattr(s, "competences", []) or [])}
    if comp.id not in existing_ids:
        s.competences.append(comp)
        db.session.commit()
        flash("Compétence ajoutée à la session.", "success")
    else:
        flash("Déjà présente en direct sur la session.", "info")

    referentiel_id = request.form.get("referentiel_id", type=int)
    q = (request.form.get("q") or "").strip()
    return redirect(url_for("activite.session_skills", session_id=s.id, referentiel_id=referentiel_id, q=q))


@bp.route("/session/<int:session_id>/skills/remove", methods=["POST"])
@login_required
def session_skill_remove(session_id: int):
    secteur = _user_secteur()
    s = SessionActivite.query.get_or_404(session_id)
    atelier = AtelierActivite.query.get_or_404(s.atelier_id)

    if s.is_deleted or atelier.is_deleted:
        flash("Cette session/atelier est dans la corbeille.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id, corbeille=1))
    if not _is_admin_global() and s.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    competence_id = request.form.get("competence_id", type=int)
    if not competence_id:
        flash("Compétence manquante.", "warning")
        return redirect(url_for("activite.session_skills", session_id=s.id))

    remaining = [c for c in (getattr(s, "competences", []) or []) if c.id != competence_id]
    if len(remaining) != len(getattr(s, "competences", []) or []):
        s.competences = remaining
        db.session.commit()
        flash("Compétence retirée de la session.", "success")
    else:
        flash("Cette compétence n'était pas ajoutée en direct sur la session.", "info")

    referentiel_id = request.form.get("referentiel_id", type=int)
    q = (request.form.get("q") or "").strip()
    return redirect(url_for("activite.session_skills", session_id=s.id, referentiel_id=referentiel_id, q=q))


# ------------------ Évaluation (grille batch) ------------------


@bp.route("/session/<int:session_id>/evaluation_batch", methods=["GET", "POST"])
@login_required
def evaluation_batch(session_id: int):
    _require_any_perm("emargement:view", "pedagogie:view")
    if request.method == "POST":
        require_perm("pedagogie:edit")(lambda: None)()
    secteur = _user_secteur()
    s = SessionActivite.query.get_or_404(session_id)
    atelier = AtelierActivite.query.get_or_404(s.atelier_id)
    if s.is_deleted or atelier.is_deleted:
        flash("Cette session/atelier est dans la corbeille.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id, corbeille=1))
    if not _is_admin_global() and s.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    presences = PresenceActivite.query.filter_by(session_id=session_id).all()
    participants = [p.participant for p in presences]
    competences = _collect_session_competences(s)

    if request.method == "POST":
        eval_date = s.rdv_date or s.date_session or date.today()
        updates = 0
        for participant in participants:
            for comp in competences:
                key = f"etat_{participant.id}_{comp.id}"
                raw = request.form.get(key)
                if raw is None or raw == "":
                    continue
                try:
                    etat_value = int(raw)
                except ValueError:
                    continue
                ev = Evaluation.query.filter_by(participant_id=participant.id, competence_id=comp.id, session_id=s.id).first()
                if not ev:
                    ev = Evaluation(
                        participant_id=participant.id,
                        competence_id=comp.id,
                        session_id=s.id,
                        user_id=current_user.id,
                        etat=etat_value,
                        date_evaluation=eval_date,
                    )
                    db.session.add(ev)
                else:
                    ev.etat = etat_value
                    ev.user_id = current_user.id
                    ev.date_evaluation = eval_date
                updates += 1
        db.session.commit()
        flash(f"{updates} évaluations enregistrées.", "success")
        return redirect(url_for("activite.evaluation_batch", session_id=s.id))

    existing = Evaluation.query.filter_by(session_id=s.id).all()
    eval_map = {(e.participant_id, e.competence_id): e.etat for e in existing}
    return render_template("activite/evaluation_batch.html", session=s, atelier=atelier, participants=participants, competences=competences, eval_map=eval_map)


# ------------------ Émargement ------------------


@bp.route("/session/<int:session_id>/emargement", methods=["GET", "POST"])
@login_required
def emargement(session_id: int):
    require_perm("emargement:view")(lambda: None)()
    if request.method == "POST":
        require_perm("emargement:edit")(lambda: None)()
    secteur = _user_secteur()
    s = SessionActivite.query.get_or_404(session_id)
    atelier = AtelierActivite.query.get_or_404(s.atelier_id)

    if s.is_deleted or atelier.is_deleted:
        flash("Cette session/atelier est dans la corbeille.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier.id, corbeille=1))
    if not _is_admin_global() and s.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    quartiers = Quartier.query.order_by(Quartier.ville.asc(), Quartier.nom.asc()).all()

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action == "update_session_modules":
            module_ids = [int(mid) for mid in request.form.getlist("module_ids") if str(mid).isdigit()]
            modules = (
                PedagogieModule.query.filter(PedagogieModule.id.in_(module_ids), PedagogieModule.actif.is_(True)).all()
                if module_ids
                else []
            )

            competence_ids = set()
            for mod in modules:
                for comp in mod.competences:
                    competence_ids.add(comp.id)

            s.modules = modules
            s.competences = (
                Competence.query.filter(Competence.id.in_(list(competence_ids))).all()
                if competence_ids
                else []
            )
            db.session.commit()
            flash("Modules (et compétences) de la session mis à jour.", "success")
            return redirect(url_for("activite.emargement", session_id=session_id))

        if action == "bulk_eval_selected":
            eval_date = s.rdv_date or s.date_session or date.today()

            participant_ids = [int(pid) for pid in request.form.getlist("participant_ids") if str(pid).isdigit()]
            competence_ids = [int(cid) for cid in request.form.getlist("competence_ids") if str(cid).isdigit()]

            if not participant_ids:
                flash("Aucun participant sélectionné.", "danger")
                return redirect(url_for("activite.emargement", session_id=session_id))
            if not competence_ids:
                flash("Aucune compétence sélectionnée.", "danger")
                return redirect(url_for("activite.emargement", session_id=session_id))

            try:
                etat_value = int(request.form.get("etat", 0))
            except Exception:
                etat_value = 0
            if etat_value not in (0, 1, 2, 3):
                etat_value = 0

            commentaire = (request.form.get("commentaire") or "").strip() or None

            present_ids = {p.participant_id for p in PresenceActivite.query.filter_by(session_id=session_id).all()}
            participant_ids = [pid for pid in participant_ids if pid in present_ids]
            if not participant_ids:
                flash("Sélection invalide (aucun participant présent sur la session).", "danger")
                return redirect(url_for("activite.emargement", session_id=session_id))

            updates = 0
            for pid in participant_ids:
                for cid in competence_ids:
                    evaluation = Evaluation.query.filter_by(
                        participant_id=pid,
                        competence_id=cid,
                        session_id=s.id,
                    ).first()
                    if evaluation:
                        evaluation.etat = etat_value
                        evaluation.commentaire = commentaire
                        evaluation.user_id = current_user.id
                        evaluation.date_evaluation = eval_date
                    else:
                        db.session.add(
                            Evaluation(
                                participant_id=pid,
                                competence_id=cid,
                                session_id=s.id,
                                user_id=current_user.id,
                                etat=etat_value,
                                date_evaluation=eval_date,
                                commentaire=commentaire,
                            )
                        )
                    updates += 1

            db.session.commit()
            flash(f"{updates} évaluation(s) appliquée(s) à la sélection.", "success")
            return redirect(url_for("activite.emargement", session_id=session_id))

        if action == "save_evaluation":
            participant_id = request.form.get("participant_id")
            if not participant_id:
                flash("Participant manquant.", "danger")
                return redirect(url_for("activite.emargement", session_id=session_id))
            participant = Participant.query.get(int(participant_id))
            if not participant:
                flash("Participant introuvable.", "danger")
                return redirect(url_for("activite.emargement", session_id=session_id))

            eval_date = s.rdv_date or s.date_session or date.today()
            competence_ids = [int(cid) for cid in request.form.getlist("competence_ids") if str(cid).isdigit()]
            for comp_id in competence_ids:
                etat = request.form.get(f"etat_{comp_id}")
                if etat is None:
                    continue
                try:
                    etat_value = int(etat)
                except ValueError:
                    continue
                commentaire = (request.form.get(f"commentaire_{comp_id}") or "").strip() or None
                evaluation = Evaluation.query.filter_by(
                    participant_id=participant.id,
                    competence_id=comp_id,
                    session_id=s.id,
                ).first()
                if evaluation:
                    evaluation.etat = etat_value
                    evaluation.commentaire = commentaire
                    evaluation.user_id = current_user.id
                    evaluation.date_evaluation = eval_date
                else:
                    db.session.add(
                        Evaluation(
                            participant_id=participant.id,
                            competence_id=comp_id,
                            session_id=s.id,
                            user_id=current_user.id,
                            etat=etat_value,
                            date_evaluation=eval_date,
                            commentaire=commentaire,
                        )
                    )
            db.session.commit()
            flash("Évaluation enregistrée.", "success")
            return redirect(url_for("activite.emargement", session_id=session_id, highlight=participant.id))

        if action == "bulk_validate":
            eval_date = s.rdv_date or s.date_session or date.today()
            session_competences = _collect_session_competences(s)
            presences = PresenceActivite.query.filter_by(session_id=session_id).all()
            for pr in presences:
                for comp in session_competences:
                    evaluation = Evaluation.query.filter_by(
                        participant_id=pr.participant_id,
                        competence_id=comp.id,
                        session_id=s.id,
                    ).first()
                    if evaluation:
                        evaluation.etat = 2
                        evaluation.user_id = current_user.id
                        evaluation.date_evaluation = eval_date
                    else:
                        db.session.add(
                            Evaluation(
                                participant_id=pr.participant_id,
                                competence_id=comp.id,
                                session_id=s.id,
                                user_id=current_user.id,
                                etat=2,
                                date_evaluation=eval_date,
                            )
                        )
            db.session.commit()
            flash("Évaluation rapide appliquée.", "success")
            return redirect(url_for("activite.emargement", session_id=session_id))

        if action == "add_participant":
            nom = (request.form.get("nom") or "").strip()
            prenom = (request.form.get("prenom") or "").strip()
            ville = (request.form.get("ville") or "").strip() or None
            adresse = (request.form.get("adresse") or "").strip() or None
            email = (request.form.get("email") or "").strip() or None
            telephone = (request.form.get("telephone") or "").strip() or None
            genre = (request.form.get("genre") or "").strip() or None
            date_naissance = request.form.get("date_naissance") or None
            type_public = (request.form.get("type_public") or "H").strip().upper() or "H"
            quartier_id = request.form.get("quartier_id") or None

            if not nom or not prenom:
                flash("Nom et prénom obligatoires.", "danger")
                return redirect(url_for("activite.emargement", session_id=session_id))

            dn = None
            if date_naissance:
                try:
                    dn = datetime.strptime(date_naissance, "%Y-%m-%d").date()
                except Exception:
                    dn = None

            qid = normalize_quartier_for_ville(ville, quartier_id)

            p = Participant(
                nom=nom,
                prenom=prenom,
                ville=ville,
                adresse=adresse,
                email=email,
                telephone=telephone,
                genre=genre,
                date_naissance=dn,
                quartier_id=qid,
                type_public=type_public,
            )
            db.session.add(p)
            db.session.commit()
            flash("Le participant a bien été créé.", "success")
            return redirect(url_for("activite.emargement", session_id=session_id, highlight=p.id))

        if action == "emarger":
            participant_id = request.form.get("participant_id")
            motif = request.form.get("motif") or None
            motif_autre = (request.form.get("motif_autre") or "").strip() or None
            signature_data = request.form.get("signature_data")

            if not participant_id:
                flash("Choisis un participant.", "danger")
                return redirect(url_for("activite.emargement", session_id=session_id))
            participant = Participant.query.get(int(participant_id))
            if not participant:
                flash("Participant introuvable.", "danger")
                return redirect(url_for("activite.emargement", session_id=session_id))

            sig_path = None
            if signature_data and signature_data.startswith("data:image"):
                try:
                    _, b64data = signature_data.split(",", 1)
                    binary = base64.b64decode(b64data)
                    sig_dir = os.path.join(current_app.instance_path, "signatures_tmp")
                    os.makedirs(sig_dir, exist_ok=True)
                    sig_filename = f"sig_s{session_id}_p{participant.id}_{int(datetime.utcnow().timestamp())}.png"
                    sig_path = os.path.join(sig_dir, sig_filename)
                    with open(sig_path, "wb") as f:
                        f.write(binary)
                except Exception:
                    sig_path = None

            try:
                pr = PresenceActivite.query.filter_by(session_id=session_id, participant_id=participant.id).first()
                if pr:
                    pr.motif = motif
                    pr.motif_autre = motif_autre
                    if sig_path:
                        pr.signature_path = sig_path
                else:
                    pr = PresenceActivite(
                        session_id=session_id,
                        participant_id=participant.id,
                        motif=motif,
                        motif_autre=motif_autre,
                        signature_path=sig_path,
                    )
                    db.session.add(pr)
                db.session.commit()
            except Exception:
                db.session.rollback()
                flash("Impossible d'enregistrer l'émargement (conflit ou erreur).", "danger")
                return redirect(url_for("activite.emargement", session_id=session_id))

            if s.session_type == "INDIVIDUEL_MENSUEL" and s.rdv_date:
                _ensure_month_capacity(atelier, s)
                generate_individuel_mensuel_docx(app=current_app, atelier=atelier, annee=s.rdv_date.year, mois=s.rdv_date.month)

            flash("Émargement enregistré.", "success")
            return redirect(url_for("activite.emargement", session_id=session_id))

        if action == "quick_passport_note":
            participant_id = request.form.get("participant_id", type=int)
            participant = Participant.query.get(participant_id) if participant_id else None
            if not participant:
                flash("Participant invalide.", "danger")
                return redirect(url_for("activite.emargement", session_id=session_id))
            contenu = (request.form.get("contenu") or "").strip()
            if not contenu:
                flash("La note rapide ne peut pas être vide.", "danger")
                return redirect(url_for("activite.emargement", session_id=session_id))
            note = PasseportNote(
                participant_id=participant.id,
                session_id=s.id,
                secteur=s.secteur,
                categorie=_normalize_note_category(request.form.get("categorie")),
                contenu=contenu,
                created_by=current_user.id,
            )
            db.session.add(note)
            db.session.commit()
            flash("Note ajoutée au passeport.", "success")
            return redirect(url_for("activite.emargement", session_id=session_id, highlight=participant.id))

    # ---- GET context ----
    participants = Participant.query.order_by(Participant.nom.asc(), Participant.prenom.asc()).limit(500).all()
    motifs = atelier.motifs() or []
    presences = PresenceActivite.query.filter_by(session_id=session_id).order_by(PresenceActivite.created_at.asc()).all()

    session_objectifs = Objectif.query.filter_by(session_id=s.id, type="operationnel").order_by(Objectif.created_at.asc()).all()
    objectifs_payload = []
    for obj in session_objectifs:
        competences = _sort_competences(obj.competences)
        objectifs_payload.append({"objectif": obj, "competences": competences})

    session_competences = _collect_session_competences(s, session_objectifs=session_objectifs)

    evaluations = Evaluation.query.filter_by(session_id=s.id).all()
    evaluation_map = {(e.participant_id, e.competence_id): e for e in evaluations}

    modules_available = (
        PedagogieModule.query.join(PlanProjetAtelierModule, PlanProjetAtelierModule.module_id == PedagogieModule.id)
        .filter(
            PlanProjetAtelierModule.atelier_id == atelier.id,
            PedagogieModule.actif.is_(True),
        )
        .distinct()
        .order_by(PedagogieModule.nom.asc())
        .all()
    )
    current_module_ids = {m.id for m in (getattr(s, "modules", []) or [])}
    schedule_edits = (
        SessionScheduleEditLog.query.filter_by(session_id=s.id)
        .order_by(SessionScheduleEditLog.edited_at.desc())
        .limit(5)
        .all()
    )

    return render_template(
        "activite/emargement.html",
        secteur=secteur,
        atelier=atelier,
        session=s,
        participants=participants,
        presences=presences,
        motifs=motifs,
        quartiers=quartiers,
        session_competences=session_competences,
        objectifs_payload=objectifs_payload,
        evaluation_map=evaluation_map,
        modules_available=modules_available,
        current_module_ids=current_module_ids,
        schedule_edits=schedule_edits,
    )


# ------------------ Kiosk ------------------


@bp.route("/session/<int:session_id>/kiosk_open", methods=["POST"])
@login_required
def kiosk_open(session_id: int):
    require_perm("ateliers:edit")(lambda: None)()
    secteur = _user_secteur()
    s = SessionActivite.query.get_or_404(session_id)
    if not _is_admin_global() and s.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    token = secrets.token_urlsafe(24)

    for _ in range(50):
        pin = f"{secrets.randbelow(10000):04d}"
        exists = SessionActivite.query.filter_by(kiosk_open=True, kiosk_pin=pin).first()
        if not exists:
            break
    else:
        pin = None

    s.kiosk_open = True
    s.kiosk_token = token
    s.kiosk_pin = pin
    s.kiosk_opened_at = datetime.utcnow()
    db.session.commit()

    flash(f"Kiosque ouvert (code: {pin}).", "success")
    return redirect(url_for("activite.emargement", session_id=session_id))


@bp.route("/session/<int:session_id>/kiosk_close", methods=["POST"])
@login_required
def kiosk_close(session_id: int):
    require_perm("ateliers:edit")(lambda: None)()
    secteur = _user_secteur()
    s = SessionActivite.query.get_or_404(session_id)
    if not _is_admin_global() and s.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    s.kiosk_open = False
    s.kiosk_pin = None
    s.kiosk_token = None
    db.session.commit()

    flash("Kiosque fermé.", "success")
    return redirect(url_for("activite.emargement", session_id=session_id))


# ------------------ Archives collectives ------------------


@bp.route("/session/<int:session_id>/generate_collectif")
@login_required
def generate_collectif(session_id: int):
    require_perm("ateliers:edit")(lambda: None)()
    secteur = _user_secteur()
    s = SessionActivite.query.get_or_404(session_id)
    atelier = AtelierActivite.query.get_or_404(s.atelier_id)
    if s.session_type != "COLLECTIF":
        flash("Uniquement pour les sessions collectives.", "warning")
        return redirect(url_for("activite.emargement", session_id=session_id))
    if not _is_admin_global() and s.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    out_docx, out_pdf = generate_collectif_docx_pdf(app=current_app, atelier=atelier, session=s)

    annee = (s.date_session.year if s.date_session else datetime.utcnow().year)
    mois = (s.date_session.month if s.date_session else datetime.utcnow().month)
    arch = ArchiveEmargement.query.filter_by(atelier_id=atelier.id, session_id=s.id, annee=annee, mois=mois).first()
    if not arch:
        arch = ArchiveEmargement(secteur=atelier.secteur, atelier_id=atelier.id, session_id=s.id, annee=annee, mois=mois)
        db.session.add(arch)
    arch.docx_path = out_docx
    arch.pdf_path = out_pdf
    arch.status = "locked"
    db.session.commit()

    if out_pdf and os.path.exists(out_pdf):
        return send_file(out_pdf, as_attachment=True)
    if out_docx and os.path.exists(out_docx):
        return send_file(out_docx, as_attachment=True)
    flash("Génération échouée.", "danger")
    return redirect(url_for("activite.emargement", session_id=session_id))


@bp.route("/session/<int:session_id>/archive/<string:kind>")
@login_required
def download_collectif_archive(session_id: int, kind: str):
    require_perm("emargement:view")(lambda: None)()
    if kind not in {"docx", "pdf"}:
        abort(404)

    secteur = _user_secteur()
    s = SessionActivite.query.get_or_404(session_id)
    atelier = AtelierActivite.query.get_or_404(s.atelier_id)

    if not _is_admin_global() and s.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    annee = (s.date_session.year if s.date_session else datetime.utcnow().year)
    mois = (s.date_session.month if s.date_session else datetime.utcnow().month)

    arch = ArchiveEmargement.query.filter_by(atelier_id=atelier.id, session_id=s.id, annee=annee, mois=mois).first()
    if not arch:
        arch = ArchiveEmargement(secteur=atelier.secteur, atelier_id=atelier.id, session_id=s.id, annee=annee, mois=mois)
        db.session.add(arch)

    need_pdf = (kind == "pdf")

    best_docx = arch.corrected_docx_path or arch.docx_path
    if not best_docx or not os.path.exists(best_docx):
        out_docx, _ = generate_collectif_docx_pdf(app=current_app, atelier=atelier, session=s)
        arch.docx_path = out_docx

    best_pdf = arch.corrected_pdf_path or arch.pdf_path
    if need_pdf and (not best_pdf or not os.path.exists(best_pdf)):
        out_docx, out_pdf = generate_collectif_docx_pdf(app=current_app, atelier=atelier, session=s)
        arch.docx_path = out_docx
        arch.pdf_path = out_pdf

    db.session.commit()

    path = _best_archive_path(arch, kind)
    if path and os.path.exists(path):
        return send_file(path, as_attachment=True)

    flash("Document introuvable : génération impossible (LibreOffice ?).", "warning")
    return redirect(url_for("activite.emargement", session_id=session_id))


@bp.route("/session/<int:session_id>/archive/upload", methods=["POST"])
@login_required
def upload_collectif_corrected(session_id: int):
    require_perm("ateliers:edit")(lambda: None)()
    secteur = _user_secteur()
    s = SessionActivite.query.get_or_404(session_id)
    atelier = AtelierActivite.query.get_or_404(s.atelier_id)
    if not _is_admin_global() and s.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    f = request.files.get("file")
    if not f or not f.filename:
        flash("Aucun fichier.", "warning")
        return redirect(url_for("activite.emargement", session_id=session_id))

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in {".docx", ".pdf"}:
        flash("Uniquement .docx ou .pdf", "warning")
        return redirect(url_for("activite.emargement", session_id=session_id))

    annee = (s.date_session.year if s.date_session else datetime.utcnow().year)
    mois = (s.date_session.month if s.date_session else datetime.utcnow().month)
    arch = ArchiveEmargement.query.filter_by(atelier_id=atelier.id, session_id=s.id, annee=annee, mois=mois).first()
    if not arch:
        arch = ArchiveEmargement(secteur=atelier.secteur, atelier_id=atelier.id, session_id=s.id, annee=annee, mois=mois)
        db.session.add(arch)
        db.session.commit()

    base_doc = arch.docx_path or ""
    if base_doc and os.path.exists(base_doc):
        base_dir = os.path.dirname(base_doc)
    else:
        base_dir = os.path.join(current_app.instance_path, "archives_emargements")
        os.makedirs(base_dir, exist_ok=True)

    safe = secure_filename(os.path.splitext(f.filename)[0])
    out_name = f"CORRIGE__{safe}{ext}"
    out_path = os.path.join(base_dir, out_name)
    f.save(out_path)

    if ext == ".docx":
        arch.corrected_docx_path = out_path
    else:
        arch.corrected_pdf_path = out_path
    db.session.commit()

    flash("Version corrigée enregistrée.", "success")
    return redirect(url_for("activite.emargement", session_id=session_id))


@bp.route("/session/<int:session_id>/archive/email", methods=["POST"])
@login_required
def email_collectif_archive(session_id: int):
    require_perm("ateliers:edit")(lambda: None)()
    secteur = _user_secteur()
    s = SessionActivite.query.get_or_404(session_id)
    atelier = AtelierActivite.query.get_or_404(s.atelier_id)
    if not _is_admin_global() and s.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    to = (request.form.get("to") or "").strip()
    if not to:
        flash("Email destinataire manquant.", "warning")
        return redirect(url_for("activite.emargement", session_id=session_id))

    annee = (s.date_session.year if s.date_session else datetime.utcnow().year)
    mois = (s.date_session.month if s.date_session else datetime.utcnow().month)
    arch = ArchiveEmargement.query.filter_by(atelier_id=atelier.id, session_id=s.id, annee=annee, mois=mois).first()
    if not arch or not arch.docx_path:
        out_docx, out_pdf = generate_collectif_docx_pdf(app=current_app, atelier=atelier, session=s)
        if not arch:
            arch = ArchiveEmargement(secteur=atelier.secteur, atelier_id=atelier.id, session_id=s.id, annee=annee, mois=mois)
            db.session.add(arch)
        arch.docx_path = out_docx
        arch.pdf_path = out_pdf
        db.session.commit()

    attachment = _best_archive_path(arch, "pdf") or _best_archive_path(arch, "docx")
    if not attachment or not os.path.exists(attachment):
        flash("Aucun document à envoyer.", "warning")
        return redirect(url_for("activite.emargement", session_id=session_id))

    cfg = current_app.config
    if not cfg.get("MAIL_HOST") or not cfg.get("MAIL_SENDER"):
        flash("SMTP non configuré (MAIL_HOST/MAIL_SENDER).", "warning")
        return redirect(url_for("activite.emargement", session_id=session_id))

    subject = request.form.get("subject") or f"Émargement - {atelier.secteur} - {atelier.nom} - {annee}-{mois:02d}"
    body = request.form.get("body") or "Ci-joint le document d'émargement."

    try:
        send_email_with_attachment(
            host=cfg.get("MAIL_HOST"),
            port=int(cfg.get("MAIL_PORT", 587)),
            username=cfg.get("MAIL_USERNAME") or None,
            password=cfg.get("MAIL_PASSWORD") or None,
            use_tls=bool(cfg.get("MAIL_USE_TLS", True)),
            sender=cfg.get("MAIL_SENDER"),
            to=to,
            subject=subject,
            body=body,
            attachment_path=attachment,
        )
        arch.last_emailed_to = to
        arch.last_emailed_at = datetime.utcnow()
        db.session.commit()
        flash("Email envoyé.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Échec envoi mail : {e}", "danger")
    return redirect(url_for("activite.emargement", session_id=session_id))


# ------------------ Archives individuels (mensuel) ------------------


@bp.route("/atelier/<int:atelier_id>/individuel/<int:annee>/<int:mois>/docx")
@login_required
def download_individuel_docx(atelier_id: int, annee: int, mois: int):
    require_perm("emargement:view")(lambda: None)()
    return download_individuel_archive(atelier_id=atelier_id, annee=annee, mois=mois, kind="docx")


@bp.route("/atelier/<int:atelier_id>/individuel/<int:annee>/<int:mois>/archive/<string:kind>")
@login_required
def download_individuel_archive(atelier_id: int, annee: int, mois: int, kind: str):
    require_perm("emargement:view")(lambda: None)()
    if kind not in {"docx", "pdf"}:
        abort(404)

    secteur = _user_secteur()
    atelier = AtelierActivite.query.get_or_404(atelier_id)

    if atelier.type_atelier != "INDIVIDUEL_MENSUEL":
        flash("Atelier non individuel mensuel.", "warning")
        return redirect(url_for("activite.index"))

    if not _is_admin_global() and atelier.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    arch = ArchiveEmargement.query.filter_by(atelier_id=atelier.id, session_id=None, annee=annee, mois=mois).first()
    if not arch:
        arch = ArchiveEmargement(secteur=atelier.secteur, atelier_id=atelier.id, session_id=None, annee=annee, mois=mois)
        db.session.add(arch)

    need_pdf = (kind == "pdf")

    best_docx = arch.corrected_docx_path or arch.docx_path
    if not best_docx or not os.path.exists(best_docx):
        arch.docx_path = generate_individuel_mensuel_docx(app=current_app, atelier=atelier, annee=annee, mois=mois)

    best_pdf = arch.corrected_pdf_path or arch.pdf_path
    if need_pdf and (not best_pdf or not os.path.exists(best_pdf)):
        arch.pdf_path = finalize_individuel_mensuel_pdf(app=current_app, atelier=atelier, annee=annee, mois=mois)

    db.session.commit()

    path = _best_archive_path(arch, kind)
    if path and os.path.exists(path):
        return send_file(path, as_attachment=True)

    flash("Document introuvable : génération échouée.", "warning")
    return redirect(url_for("activite.sessions", atelier_id=atelier_id))


@bp.route("/atelier/<int:atelier_id>/individuel/<int:annee>/<int:mois>/archive/upload", methods=["POST"])
@login_required
def upload_individuel_corrected(atelier_id: int, annee: int, mois: int):
    require_perm("ateliers:edit")(lambda: None)()
    secteur = _user_secteur()
    atelier = AtelierActivite.query.get_or_404(atelier_id)
    if atelier.type_atelier != "INDIVIDUEL_MENSUEL":
        flash("Atelier non individuel mensuel.", "warning")
        return redirect(url_for("activite.index"))
    if not _is_admin_global() and atelier.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    f = request.files.get("file")
    if not f or not f.filename:
        flash("Aucun fichier.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier_id))
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in {".docx", ".pdf"}:
        flash("Uniquement .docx ou .pdf", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier_id))

    arch = ArchiveEmargement.query.filter_by(atelier_id=atelier.id, session_id=None, annee=annee, mois=mois).first()
    if not arch:
        arch = ArchiveEmargement(secteur=atelier.secteur, atelier_id=atelier.id, session_id=None, annee=annee, mois=mois)
        db.session.add(arch)
        db.session.commit()

    base_doc = arch.docx_path or ""
    if base_doc and os.path.exists(base_doc):
        base_dir = os.path.dirname(base_doc)
    else:
        base_dir = os.path.join(current_app.instance_path, "archives_emargements")
        os.makedirs(base_dir, exist_ok=True)

    safe = secure_filename(os.path.splitext(f.filename)[0])
    out_name = f"CORRIGE__{safe}{ext}"
    out_path = os.path.join(base_dir, out_name)
    f.save(out_path)

    if ext == ".docx":
        arch.corrected_docx_path = out_path
    else:
        arch.corrected_pdf_path = out_path
    db.session.commit()
    flash("Version corrigée enregistrée.", "success")
    return redirect(url_for("activite.sessions", atelier_id=atelier_id))


@bp.route("/atelier/<int:atelier_id>/individuel/<int:annee>/<int:mois>/archive/email", methods=["POST"])
@login_required
def email_individuel_archive(atelier_id: int, annee: int, mois: int):
    require_perm("ateliers:edit")(lambda: None)()
    secteur = _user_secteur()
    atelier = AtelierActivite.query.get_or_404(atelier_id)
    if atelier.type_atelier != "INDIVIDUEL_MENSUEL":
        flash("Atelier non individuel mensuel.", "warning")
        return redirect(url_for("activite.index"))
    if not _is_admin_global() and atelier.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    to = (request.form.get("to") or "").strip()
    if not to:
        flash("Email destinataire manquant.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier_id))

    arch = ArchiveEmargement.query.filter_by(atelier_id=atelier.id, session_id=None, annee=annee, mois=mois).first()
    if not arch or not arch.docx_path:
        out_docx = generate_individuel_mensuel_docx(app=current_app, atelier=atelier, annee=annee, mois=mois)
        out_pdf = finalize_individuel_mensuel_pdf(app=current_app, atelier=atelier, annee=annee, mois=mois)
        if not arch:
            arch = ArchiveEmargement(secteur=atelier.secteur, atelier_id=atelier.id, session_id=None, annee=annee, mois=mois)
            db.session.add(arch)
        arch.docx_path = out_docx
        arch.pdf_path = out_pdf
        db.session.commit()

    attachment = _best_archive_path(arch, "pdf") or _best_archive_path(arch, "docx")
    if not attachment or not os.path.exists(attachment):
        flash("Aucun document à envoyer.", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier_id))

    cfg = current_app.config
    if not cfg.get("MAIL_HOST") or not cfg.get("MAIL_SENDER"):
        flash("SMTP non configuré (MAIL_HOST/MAIL_SENDER).", "warning")
        return redirect(url_for("activite.sessions", atelier_id=atelier_id))

    subject = request.form.get("subject") or f"Émargement - {atelier.secteur} - {atelier.nom} - {annee}-{mois:02d}"
    body = request.form.get("body") or "Ci-joint le document d'émargement."

    try:
        send_email_with_attachment(
            host=cfg.get("MAIL_HOST"),
            port=int(cfg.get("MAIL_PORT", 587)),
            username=cfg.get("MAIL_USERNAME") or None,
            password=cfg.get("MAIL_PASSWORD") or None,
            use_tls=bool(cfg.get("MAIL_USE_TLS", True)),
            sender=cfg.get("MAIL_SENDER"),
            to=to,
            subject=subject,
            body=body,
            attachment_path=attachment,
        )
        arch.last_emailed_to = to
        arch.last_emailed_at = datetime.utcnow()
        db.session.commit()
        flash("Email envoyé.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Échec envoi mail : {e}", "danger")
    return redirect(url_for("activite.sessions", atelier_id=atelier_id))


@bp.route("/atelier/<int:atelier_id>/individuel/<int:annee>/<int:mois>/finalize")
@login_required
def finalize_individuel(atelier_id: int, annee: int, mois: int):
    require_perm("ateliers:edit")(lambda: None)()
    secteur = _user_secteur()
    atelier = AtelierActivite.query.get_or_404(atelier_id)
    if atelier.type_atelier != "INDIVIDUEL_MENSUEL":
        flash("Atelier non individuel mensuel.", "warning")
        return redirect(url_for("activite.index"))
    if not _is_admin_global() and atelier.secteur != secteur:
        flash("Accès refusé.", "danger")
        return redirect(url_for("activite.index"))

    out_docx = generate_individuel_mensuel_docx(app=current_app, atelier=atelier, annee=annee, mois=mois)
    out_pdf = finalize_individuel_mensuel_pdf(app=current_app, atelier=atelier, annee=annee, mois=mois)

    cap = AtelierCapaciteMois.query.filter_by(atelier_id=atelier.id, annee=annee, mois=mois).first()
    if cap:
        cap.locked = True

    arch = ArchiveEmargement.query.filter_by(atelier_id=atelier.id, session_id=None, annee=annee, mois=mois).first()
    if not arch:
        arch = ArchiveEmargement(secteur=atelier.secteur, atelier_id=atelier.id, session_id=None, annee=annee, mois=mois)
        db.session.add(arch)
    arch.docx_path = out_docx
    arch.pdf_path = out_pdf
    arch.status = "locked" if out_pdf else "open"
    db.session.commit()

    if out_pdf and os.path.exists(out_pdf):
        return send_file(out_pdf, as_attachment=True)
    if out_docx and os.path.exists(out_docx):
        flash("PDF non généré (LibreOffice manquant ?). Téléchargement du DOCX.", "warning")
        return send_file(out_docx, as_attachment=True)
    flash("Finalisation échouée.", "danger")
    return redirect(url_for("activite.sessions", atelier_id=atelier_id))
