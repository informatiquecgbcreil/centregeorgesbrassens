from __future__ import annotations

from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from ..rbac import require_perm, can
from ..rbac import can_access_secteur

from app.extensions import db
from app.models import Participant, PresenceActivite, SessionActivite, Evaluation, Quartier
from app.services.quartiers import normalize_quartier_for_ville
from sqlalchemy import select, exists, and_
from app.utils.delete_guard import commit_delete


bp = Blueprint("participants", __name__, url_prefix="/participants")


def _current_secteur() -> str:
    return (getattr(current_user, "secteur_assigne", "") or "").strip()


def _is_global_role() -> bool:
    return current_user.has_perm("participants:view_all") or current_user.has_perm("scope:all_secteurs")


def _can_read_participant(p: Participant) -> bool:
    return bool(can('participants:view') or can('participants:edit') or can('participants:delete'))


def _can_edit_participant(p: Participant) -> bool:
    return bool(can('participants:edit'))


def _can_see_participant(p: Participant) -> bool:
    # Visible (ancienne logique) : créé par secteur OU déjà présent dans secteur
    # Utilisé uniquement pour les listings "dans mon secteur", PAS pour l'édition.
    if _is_global_role():
        return True
    if not current_user.has_perm("participants:view_all"):
        sec = _current_secteur()
        if not sec:
            return False
        if (p.created_secteur or "") == sec:
            return True
        has_presence = (
            db.session.query(PresenceActivite.id)
            .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
            .filter(PresenceActivite.participant_id == p.id)
            .filter(SessionActivite.secteur == sec)
            .first()
        )
        return bool(has_presence)
    return False


@bp.route("/")
@login_required
def list_participants():
    if False:
        abort(403)

    q = (request.args.get("q") or "").strip()
    scope = (request.args.get("scope") or "").strip()  # ""/secteur, created, annuaire

    # NEW filtres dropdown
    f_ville = (request.args.get("ville") or "").strip()
    f_quartier_id = (request.args.get("quartier_id") or "").strip()
    f_genre = (request.args.get("genre") or "").strip()             # "Homme" / "Femme" / ""
    f_type_public = (request.args.get("type_public") or "").strip() # "H"/"F"/"A"/...
    f_presence = (request.args.get("presence") or "").strip()       # "" / "with" / "without"
    f_created_secteur = (request.args.get("created_secteur") or "").strip()  # global only

    # Annuaire global : uniquement si recherche (>=2), sinon on retombe en sectoriel
    if scope == "annuaire" and (not q or len(q) < 2):
        scope = "secteur"

    # ---------------------------------------------------------
    # 1) BASE RBAC/SCOPE (sans les dropdowns)
    # ---------------------------------------------------------
    participants_q = Participant.query

    if not current_user.has_perm("participants:view_all"):
        sec = _current_secteur()
        if not sec:
            abort(403)

        # En mode annuaire (avec recherche), pas de restriction sectorielle
        if not (scope == "annuaire" and q and len(q) >= 2):
            if scope == "created":
                participants_q = participants_q.filter(Participant.created_secteur == sec)
            else:
                subq_presence_ids = (
                    db.session.query(PresenceActivite.participant_id)
                    .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
                    .filter(SessionActivite.secteur == sec)
                    .distinct()
                )
                participants_q = participants_q.filter(
                    (Participant.created_secteur == sec) | (Participant.id.in_(subq_presence_ids))
                )
    else:
        # rôle global : option filtre secteur (ancien comportement)
        if scope == "secteur":
            sec = (request.args.get("secteur") or "").strip()
            if sec:
                participants_q = participants_q.filter(Participant.created_secteur == sec)

        if f_created_secteur:
            participants_q = participants_q.filter(Participant.created_secteur == f_created_secteur)

    # ---------------------------------------------------------
    # 2) Construire BASE_Q pour les dropdowns
    #    (même RBAC/scope mais SANS les dropdowns pour ne pas vider les listes)
    #    (et tu choisis si tu veux inclure q ou pas : moi je le laisse OFF)
    # ---------------------------------------------------------
    base_q = Participant.query

    if not current_user.has_perm("participants:view_all"):
        sec = _current_secteur()
        if not sec:
            abort(403)

        if not (scope == "annuaire" and q and len(q) >= 2):
            if scope == "created":
                base_q = base_q.filter(Participant.created_secteur == sec)
            else:
                subq_presence_ids = (
                    db.session.query(PresenceActivite.participant_id)
                    .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
                    .filter(SessionActivite.secteur == sec)
                    .distinct()
                )
                base_q = base_q.filter(
                    (Participant.created_secteur == sec) | (Participant.id.in_(subq_presence_ids))
                )
        # en annuaire : base_q reste globale (mais ton annuaire n'affiche déjà rien sans q)
    else:
        if scope == "secteur":
            sec2 = (request.args.get("secteur") or "").strip()
            if sec2:
                base_q = base_q.filter(Participant.created_secteur == sec2)

        if f_created_secteur:
            base_q = base_q.filter(Participant.created_secteur == f_created_secteur)

    # Sous-requête des IDs (évite select_from() + compatible PG)
    base_ids_sq = base_q.with_entities(Participant.id).subquery()
    base_ids_select = select(base_ids_sq.c.id)

    # ---------------------------------------------------------
    # 3) Dropdowns (filtrés sur base_q)
    # ---------------------------------------------------------
    villes = (
        db.session.query(Participant.ville)
        .filter(Participant.ville.isnot(None))
        .filter(Participant.ville != "")
        .filter(Participant.id.in_(base_ids_select))
        .distinct()
        .order_by(Participant.ville.asc())
        .all()
    )
    villes = [v[0] for v in villes]

    quartiers = (
        db.session.query(Quartier)
        .join(Participant, Participant.quartier_id == Quartier.id)
        .filter(Participant.id.in_(base_ids_select))
        .distinct()
        .order_by(Quartier.ville.asc(), Quartier.nom.asc())
        .all()
    )

    genres = (
        db.session.query(Participant.genre)
        .filter(Participant.genre.isnot(None))
        .filter(Participant.genre != "")
        .filter(Participant.id.in_(base_ids_select))
        .distinct()
        .order_by(Participant.genre.asc())
        .all()
    )
    genres = [g[0] for g in genres]

    types_public = (
        db.session.query(Participant.type_public)
        .filter(Participant.type_public.isnot(None))
        .filter(Participant.type_public != "")
        .filter(Participant.id.in_(base_ids_select))
        .distinct()
        .order_by(Participant.type_public.asc())
        .all()
    )
    types_public = [t[0] for t in types_public]

    # ---------------------------------------------------------
    # 4) Appliquer les dropdowns SUR participants_q (la vraie liste)
    # ---------------------------------------------------------
    if f_ville:
        participants_q = participants_q.filter(Participant.ville == f_ville)

    if f_quartier_id:
        try:
            qid = int(f_quartier_id)
            participants_q = participants_q.filter(Participant.quartier_id == qid)
        except Exception:
            pass

    if f_genre:
        participants_q = participants_q.filter(Participant.genre == f_genre)

    if f_type_public:
        participants_q = participants_q.filter(Participant.type_public == f_type_public)

    # Présence: EXISTS propre
    has_presence = exists().where(PresenceActivite.participant_id == Participant.id)

    if f_presence == "with":
        participants_q = participants_q.filter(has_presence)
    elif f_presence == "without":
        participants_q = participants_q.filter(~has_presence)

    # ---------------------------------------------------------
    # 5) Recherche texte (tous rôles)
    # ---------------------------------------------------------
    if q:
        like = f"%{q.lower()}%"
        participants_q = participants_q.filter(
            db.or_(
                db.func.lower(Participant.nom).like(like),
                db.func.lower(Participant.prenom).like(like),
                db.func.lower(db.func.coalesce(Participant.email, "")).like(like),
                db.func.lower(db.func.coalesce(Participant.telephone, "")).like(like),
            )
        )

    items = participants_q.order_by(Participant.nom.asc(), Participant.prenom.asc()).limit(1000).all()

    return render_template(
        "participants/list.html",
        items=items,
        q=q,
        scope=scope,
        secteur=_current_secteur(),

        # dropdowns
        villes=villes,
        quartiers=quartiers,
        genres=genres,
        types_public=types_public,

        # valeurs sélectionnées
        f_ville=f_ville,
        f_quartier_id=f_quartier_id,
        f_genre=f_genre,
        f_type_public=f_type_public,
        f_presence=f_presence,
        f_created_secteur=f_created_secteur,

        is_global=_is_global_role(),
    )


@bp.route("/search")
@login_required
def search_participants():
    """Annuaire global (lecture seule) pour l'auto-complétion côté émargement."""
    if False:
        abort(403)

    q = (request.args.get("q") or "").strip()
    if not q or len(q) < 2:
        return {"items": []}

    like = f"%{q.lower()}%"
    participants_q = Participant.query.filter(
        db.or_(
            db.func.lower(Participant.nom).like(like),
            db.func.lower(Participant.prenom).like(like),
            db.func.lower(db.func.coalesce(Participant.email, "")).like(like),
            db.func.lower(db.func.coalesce(Participant.telephone, "")).like(like),
        )
    )

    items = (
        participants_q.order_by(Participant.nom.asc(), Participant.prenom.asc())
        .limit(30)
        .all()
    )

    def _year(d):
        try:
            return d.year if d else None
        except Exception:
            return None

    return {
        "items": [
            {
                "id": p.id,
                "nom": p.nom,
                "prenom": p.prenom,
                "annee_naissance": _year(getattr(p, "date_naissance", None)),
                "ville": getattr(p, "ville", None),
                "created_secteur": getattr(p, "created_secteur", None),
            }
            for p in items
        ]
    }


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new_participant():
    if False:
        abort(403)

    if request.method == "POST":
        nom = (request.form.get("nom") or "").strip()
        prenom = (request.form.get("prenom") or "").strip()
        if not nom or not prenom:
            flash("Le nom et le prénom sont obligatoires.", "err")
            return redirect(url_for("participants.new_participant"))

        p = Participant(
            nom=nom,
            prenom=prenom,
            adresse=(request.form.get("adresse") or "").strip() or None,
            ville=(request.form.get("ville") or "").strip() or None,
            email=(request.form.get("email") or "").strip() or None,
            telephone=(request.form.get("telephone") or "").strip() or None,
            genre=(request.form.get("genre") or "").strip() or None,
            type_public=(request.form.get("type_public") or "H").strip() or "H",
            created_by_user_id=getattr(current_user, "id", None),
            created_secteur=(
                _current_secteur()
                if not current_user.has_perm("participants:view_all")
                else (request.form.get("created_secteur") or "").strip() or None
            ),
        )
        quartier_id = request.form.get("quartier_id") or None
        p.quartier_id = normalize_quartier_for_ville(p.ville, quartier_id)

        d = (request.form.get("date_naissance") or "").strip()
        if d:
            try:
                p.date_naissance = datetime.strptime(d, "%Y-%m-%d").date()
            except Exception:
                pass

        db.session.add(p)
        db.session.commit()
        flash("Le participant a bien été créé.", "ok")
        return redirect(url_for("participants.edit_participant", participant_id=p.id))

    quartiers = Quartier.query.order_by(Quartier.ville.asc(), Quartier.nom.asc()).all()
    return render_template(
        "participants/form.html",
        item=None,
        secteur=_current_secteur(),
        is_editable=True,
        quartiers=quartiers,
    )


@bp.route("/<int:participant_id>/edit", methods=["GET", "POST"])
@login_required
def edit_participant(participant_id: int):
    if False:
        abort(403)

    p = Participant.query.get_or_404(participant_id)

    # Lecture globale autorisée (annuaire), mais édition verrouillée
    if not _can_read_participant(p):
        abort(403)

    is_editable = _can_edit_participant(p)

    if request.method == "POST":
        if not is_editable:
            abort(403)

        p.nom = (request.form.get("nom") or "").strip() or p.nom
        p.prenom = (request.form.get("prenom") or "").strip() or p.prenom
        p.adresse = (request.form.get("adresse") or "").strip() or None
        p.ville = (request.form.get("ville") or "").strip() or None
        p.email = (request.form.get("email") or "").strip() or None
        p.telephone = (request.form.get("telephone") or "").strip() or None
        p.genre = (request.form.get("genre") or "").strip() or None
        p.type_public = (request.form.get("type_public") or p.type_public or "H").strip() or "H"
        quartier_id = request.form.get("quartier_id") or None
        p.quartier_id = normalize_quartier_for_ville(p.ville, quartier_id)

        d = (request.form.get("date_naissance") or "").strip()
        if d:
            try:
                p.date_naissance = datetime.strptime(d, "%Y-%m-%d").date()
            except Exception:
                pass
        else:
            p.date_naissance = None

        # finance/directrice peuvent requalifier created_secteur
        if _is_global_role():
            p.created_secteur = (request.form.get("created_secteur") or "").strip() or None

        db.session.commit()
        flash("Le participant a bien été mis à jour.", "ok")
        return redirect(url_for("participants.edit_participant", participant_id=p.id))

    quartiers = Quartier.query.order_by(Quartier.ville.asc(), Quartier.nom.asc()).all()
    return render_template(
        "participants/form.html",
        item=p,
        secteur=_current_secteur(),
        is_editable=is_editable,
        quartiers=quartiers,
    )


@bp.route("/<int:participant_id>/anonymize", methods=["POST"])
@login_required
def anonymize_participant(participant_id: int):
    if False:
        abort(403)

    p = Participant.query.get_or_404(participant_id)
    if not _can_edit_participant(p):
        abort(403)

    p.nom = "ANONYME"
    p.prenom = f"P{p.id}"
    p.adresse = None
    p.ville = None
    p.email = None
    p.telephone = None

    strict = (request.form.get("strict") or "").strip() == "1"
    if strict and _is_global_role():
        p.genre = None
        p.date_naissance = None
        p.quartier_id = None
        p.type_public = "H"

    db.session.commit()
    flash("Le participant a bien été anonymisé. Les statistiques sont conservées.", "ok")
    return redirect(url_for("participants.edit_participant", participant_id=p.id))


@bp.route("/<int:participant_id>/delete", methods=["POST"])
@login_required
def delete_participant(participant_id: int):
    if False:
        abort(403)
    if not can('participants:delete'):
        abort(403)

    p = Participant.query.get_or_404(participant_id)
    if not _can_edit_participant(p):
        abort(403)

    # garde-fou : un responsable secteur ne supprime pas si le participant existe ailleurs
    if not current_user.has_perm("participants:view_all"):
        sec = _current_secteur()
        other = (
            db.session.query(PresenceActivite.id)
            .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
            .filter(PresenceActivite.participant_id == p.id)
            .filter(SessionActivite.secteur != sec)
            .first()
        )
        if other:
            flash("La suppression est refusée : ce participant est présent dans d'autres secteurs. Utilisez l'anonymisation à la place.", "err")
            return redirect(url_for("participants.edit_participant", participant_id=p.id))

    db.session.query(PresenceActivite).filter(PresenceActivite.participant_id == p.id).delete(synchronize_session=False)
    db.session.query(Evaluation).filter(Evaluation.participant_id == p.id).delete(synchronize_session=False)
    db.session.delete(p)
    commit_delete(
        f"le participant « {p.nom_complet()} »",
        "Participant supprimé définitivement.",
        success_category="warning",
        blocked_message=f"Impossible de supprimer le participant « {p.nom_complet()} » : il est encore utilisé ailleurs. Utilisez plutôt l'anonymisation si vous souhaitez conserver l'historique.",
    )
    return redirect(url_for("participants.list_participants"))
    
    import re
from flask import current_app

_FAKE_PREFIXES = (
    "CAPACITE", "CAPACITÉ",
    "DUREE", "DURÉE",
    "NOMBRE",
    "TAUX",
    "MOYENNE",
    "TOTAL",
    "NB ",
    "? ",
    "STAT",
    "NBRE ",
)

@bp.route("/cleanup-fakes", methods=["POST"])
@login_required
def cleanup_fakes():
    # Autorisation : tu peux choisir soit participants:delete, soit ateliers:sync
    if not (can("participants:delete") or can("ateliers:sync")):
        abort(403)

    # Mode prévisualisation (par défaut = 1)
    preview = (request.form.get("preview") or "1").strip() in ("1", "true", "on", "yes")

    # On limite au secteur de l'utilisateur (sauf rôles globaux)
    sec = _current_secteur()
    if not _is_global_role() and not sec:
        abort(403)

    # Regex simple : commence par un des mots-clés (avec ou sans accents), + éventuellement " ?"
    # Exemple: "CAPACITE D'ACCUEIL ?" / "DUREE DE L'ACTIVITE ?"
    def is_fake(p: Participant) -> bool:
        nom = (p.nom or "").strip()
        prenom = (p.prenom or "").strip()

        # Souvent les faux ont un prénom vide / bizarre
        # (si chez toi c'est toujours prenom rempli, on peut retirer ce critère)
        if prenom and len(prenom) > 1:
            return False

        up = nom.upper()

        # commence par un préfixe "métier"
        if any(up.startswith(pref) for pref in _FAKE_PREFIXES):
            return True

        # ou contient un gros marqueur Excel foireux
        if "?" in nom or "??" in nom:
            # on évite de supprimer un vrai "?" improbable, mais c'est ultra rare
            return True

        return False

    # Base query (filtre secteur si non global)
    q = Participant.query
    if not _is_global_role():
        q = q.filter(Participant.created_secteur == sec)

    # On charge un lot raisonnable
    candidates = q.order_by(Participant.id.desc()).limit(5000).all()

    # On ne supprime jamais si le participant a des présences (sécurité)
    fake_ids = []
    fake_labels = []
    for p in candidates:
        if not is_fake(p):
            continue

        has_presence = (
            db.session.query(PresenceActivite.id)
            .filter(PresenceActivite.participant_id == p.id)
            .first()
        )
        if has_presence:
            continue

        fake_ids.append(p.id)
        fake_labels.append(f"{p.id} — {p.nom} {p.prenom}".strip())

    if not fake_ids:
        flash("Aucune fiche importée par erreur n'a été détectée.", "success")
        return redirect(url_for("participants.list_participants"))

    if preview:
        # On montre juste un échantillon, sans supprimer
        sample = fake_labels[:20]
        flash(
            f"Prévisualisation : {len(fake_ids)} faux participant(s) détecté(s). "
            f"Exemples: {', '.join(sample)}",
            "warning"
        )
        flash("Relancez l'action sans prévisualisation pour supprimer réellement les fiches détectées.", "info")
        return redirect(url_for("participants.list_participants"))

    # Suppression réelle (sans présences, donc safe FK)
    try:
        db.session.query(Evaluation).filter(Evaluation.participant_id.in_(fake_ids)).delete(synchronize_session=False)
        # PresenceActivite devrait être vide par sécurité, mais on nettoie quand même
        db.session.query(PresenceActivite).filter(PresenceActivite.participant_id.in_(fake_ids)).delete(synchronize_session=False)

        db.session.query(Participant).filter(Participant.id.in_(fake_ids)).delete(synchronize_session=False)
        db.session.commit()
        flash(f"Le nettoyage a bien été effectué : {len(fake_ids)} fiche(s) ont été supprimées.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("cleanup_fakes failed")
        flash(f"Le nettoyage a échoué : {e}", "danger")

    return redirect(url_for("participants.list_participants"))


import re
from collections import defaultdict
from difflib import SequenceMatcher

def _norm(s: str | None) -> str:
    s = (s or "").strip().lower()
    # enlève accents simples (sans dépendance)
    s = s.replace("é", "e").replace("è", "e").replace("ê", "e").replace("ë", "e")
    s = s.replace("à", "a").replace("â", "a")
    s = s.replace("î", "i").replace("ï", "i")
    s = s.replace("ô", "o")
    s = s.replace("ù", "u").replace("û", "u").replace("ü", "u")
    # garde lettres/chiffres/espace
    s = re.sub(r"[^a-z0-9\s\-']", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _phone_norm(s: str | None) -> str:
    s = re.sub(r"\D+", "", (s or ""))
    # garde les 10 derniers chiffres (pratique FR)
    return s[-10:] if len(s) >= 10 else s

def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

@bp.route("/duplicates", methods=["GET"])
@login_required
def duplicates():
    if not _can_read_participant(None):  # simple garde-fou
        abort(403)

    # réglages
    mode = (request.args.get("mode") or "certain").strip()  # certain / probable
    threshold = float(request.args.get("t") or "0.90")      # pour probable
    limit = int(request.args.get("limit") or "2000")

    # scope secteur pour non-global
    sec = _current_secteur()
    q = Participant.query
    if not _is_global_role():
        if not sec:
            abort(403)
        q = q.filter(Participant.created_secteur == sec)

    items = q.order_by(Participant.id.desc()).limit(limit).all()

    # --- doublons certains ---
    by_name = defaultdict(list)
    by_email = defaultdict(list)
    by_phone = defaultdict(list)

    for p in items:
        key_name = f"{_norm(p.nom)}|{_norm(p.prenom)}"
        if _norm(p.nom) and _norm(p.prenom):
            by_name[key_name].append(p)

        em = _norm(getattr(p, "email", None))
        if em:
            by_email[em].append(p)

        ph = _phone_norm(getattr(p, "telephone", None))
        if ph:
            by_phone[ph].append(p)

    groups = []
    def push_groups(source: str, dct):
        for k, arr in dct.items():
            if len(arr) >= 2:
                groups.append({
                    "type": source,
                    "key": k,
                    "items": sorted(arr, key=lambda x: (x.nom or "", x.prenom or "", x.id))
                })

    push_groups("nom_prenom", by_name)
    push_groups("email", by_email)
    push_groups("telephone", by_phone)

    # --- doublons probables (approx) ---
    probable = []
    if mode == "probable":
        # on compare uniquement sur nom/prénom normalisés
        normed = [(p, _norm(p.nom), _norm(p.prenom)) for p in items if _norm(p.nom) and _norm(p.prenom)]
        # petit tri pour limiter comparaisons
        normed.sort(key=lambda x: (x[1][:3], x[2][:3], x[0].id))

        # comparaison locale par “fenêtre” (évite O(n²) complet)
        for i in range(len(normed)):
            p1, n1, pr1 = normed[i]
            base1 = f"{n1} {pr1}"
            for j in range(i+1, min(i+15, len(normed))):
                p2, n2, pr2 = normed[j]
                # si les 3 premières lettres divergent trop, on stop
                if n2[:3] != n1[:3] and pr2[:3] != pr1[:3]:
                    continue
                base2 = f"{n2} {pr2}"
                s = _sim(base1, base2)
                if s >= threshold and p1.id != p2.id:
                    probable.append({
                        "score": round(s, 3),
                        "a": p1,
                        "b": p2
                    })

        probable = sorted(probable, key=lambda x: x["score"], reverse=True)[:200]

    # tri groupes certains : les plus gros d’abord
    groups.sort(key=lambda g: (-len(g["items"]), g["type"], g["key"]))

    return render_template(
        "participants/duplicates.html",
        groups=groups,
        probable=probable,
        mode=mode,
        threshold=threshold,
        secteur=sec,
        is_global=_is_global_role(),
    )


@bp.route("/merge", methods=["POST"])
@login_required
def merge_participants():
    if not can("participants:delete"):
        abort(403)

    keep_id = int(request.form.get("keep_id") or 0)
    merge_ids = request.form.getlist("merge_ids")
    merge_ids = [int(x) for x in merge_ids if str(x).isdigit() and int(x) != keep_id]

    if not keep_id or not merge_ids:
        flash("La fusion est impossible : la sélection est invalide.", "danger")
        return redirect(url_for("participants.duplicates"))

    keep = Participant.query.get_or_404(keep_id)
    victims = Participant.query.filter(Participant.id.in_(merge_ids)).all()

    try:
        # 1) Transférer Evaluations (safe)
        db.session.query(Evaluation).filter(Evaluation.participant_id.in_(merge_ids))\
            .update({Evaluation.participant_id: keep_id}, synchronize_session=False)

        # 2) Présences: éviter les collisions AVANT l'update
        # sessions déjà présentes chez keep
        keep_session_ids = set(
            sid for (sid,) in db.session.query(PresenceActivite.session_id)
            .filter(PresenceActivite.participant_id == keep_id)
            .all()
        )

        if keep_session_ids:
            # supprimer les présences des victims qui collent sur les mêmes sessions
            db.session.query(PresenceActivite)\
                .filter(PresenceActivite.participant_id.in_(merge_ids))\
                .filter(PresenceActivite.session_id.in_(keep_session_ids))\
                .delete(synchronize_session=False)

        # maintenant, update sans risque
        db.session.query(PresenceActivite)\
            .filter(PresenceActivite.participant_id.in_(merge_ids))\
            .update({PresenceActivite.participant_id: keep_id}, synchronize_session=False)

        # 3) Delete des participants doublons (ils n'ont plus de liens)
        for v in victims:
            db.session.delete(v)

        db.session.commit()
        flash(f"La fusion a bien été effectuée : le participant #{keep_id} a absorbé {len(merge_ids)} doublon(s).", "success")
        return redirect(url_for("participants.edit_participant", participant_id=keep_id))

    except Exception as e:
        db.session.rollback()
        flash(f"La fusion a échoué : {e}", "danger")
        return redirect(url_for("participants.duplicates"))
