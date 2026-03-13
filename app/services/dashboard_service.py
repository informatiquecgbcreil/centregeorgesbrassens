from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Tuple

from flask import url_for
from werkzeug.routing import BuildError
from sqlalchemy import func

from app.models import (
    Subvention,
    Depense,
    LigneBudget,
    SessionActivite,
    PresenceActivite,
    Participant,
    Quartier,
)


AGE_BUCKETS: List[Tuple[str, int | None, int | None]] = [
    ("0-5 ans", 0, 5),
    ("6-11 ans", 6, 11),
    ("12-17 ans", 12, 17),
    ("18-25 ans", 18, 25),
    ("26-39 ans", 26, 39),
    ("40-59 ans", 40, 59),
    ("60 ans et +", 60, None),
]


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _last_n_months(n: int, today: date | None = None) -> List[Tuple[int, int]]:
    today = today or date.today()
    y, m = today.year, today.month
    out: List[Tuple[int, int]] = []
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    out.reverse()
    return out


def _session_effective_date_expr():
    return func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session)


def _session_effective_date(session: SessionActivite):
    return session.rdv_date or session.date_session


def _normalize_gender(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return "Non renseigné"
    raw = raw.replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
    if raw in {"f", "femme", "feminin", "feminin", "female", "woman"}:
        return "Femmes"
    if raw in {"h", "m", "homme", "masculin", "male", "man"}:
        return "Hommes"
    if raw in {"autre", "non binaire", "non-binaire", "nb", "x"}:
        return "Autre"
    return "Non renseigné"


def _compute_age(dob: date | None, ref_date: date) -> int | None:
    if not dob:
        return None
    years = ref_date.year - dob.year
    if (ref_date.month, ref_date.day) < (dob.month, dob.day):
        years -= 1
    if years < 0 or years > 120:
        return None
    return years


def _age_bucket(age: int | None) -> str | None:
    if age is None:
        return None
    for label, low, high in AGE_BUCKETS:
        if low is not None and age < low:
            continue
        if high is not None and age > high:
            continue
        return label
    return None


def _clean_city(q_ville: str | None, participant_ville: str | None) -> str:
    city = (q_ville or participant_ville or "").strip()
    return city if city else "Ville non renseignée"


def _clean_quartier(value: str | None) -> str:
    quartier = (value or "").strip()
    return quartier if quartier else "Quartier non renseigné"


def _resolve_period(period_key: str | None, *, days: int, budget_year: int, today: date) -> tuple[str, date, date, str, int]:
    key = (period_key or "").strip().lower()
    if key == "year":
        since = date(budget_year, 1, 1)
        until = date(budget_year, 12, 31)
        if budget_year == today.year:
            until = today
        effective_days = max(1, (until - since).days + 1)
        return "year", since, until, f"année {budget_year}", effective_days

    if key not in {"30", "90", "365"}:
        key = str(int(days or 90))
    effective_days = int(key)
    since = today - timedelta(days=max(effective_days - 1, 0))
    return key, since, today, f"{effective_days} jours", effective_days


def build_dashboard_context(
    user,
    *,
    days: int = 90,
    budget_year: int | None = None,
    period_key: str | None = None,
) -> Dict[str, Any]:
    """Construit un contexte riche pour le dashboard."""

    def _safe(endpoint: str, fallback: str = "#", **values) -> str:
        try:
            return url_for(endpoint, **values)
        except BuildError:
            return fallback

    has_perm = getattr(user, "has_perm", None)
    has_scope_all = callable(has_perm) and has_perm("scope:all_secteurs")
    has_business_access = callable(has_perm) and any(
        has_perm(p) for p in ("subventions:view", "projets:view", "stats:view", "statsimpact:view")
    )

    budget_year = int(budget_year or date.today().year)
    today = date.today()
    current_period_key, since_date, until_date, period_label, effective_days = _resolve_period(
        period_key,
        days=days,
        budget_year=budget_year,
        today=today,
    )

    if callable(has_perm) and has_perm("admin:users") and not has_business_access:
        return {
            "mode": "admin_tech",
            "kpis": {},
            "alerts": [],
            "shortcuts": [
                {"label": "Gérer l’équipe", "url": _safe("admin.users"), "icon": "🛠️"},
            ],
            "recents": {"depenses": [], "sessions": [], "participants": []},
            "charts": {},
            "days": effective_days,
            "budget_year": budget_year,
            "period_key": current_period_key,
            "period_label": period_label,
        }

    subs_q = Subvention.query.filter_by(est_archive=False).filter(Subvention.annee_exercice == budget_year)
    if not has_scope_all:
        subs_q = subs_q.filter(Subvention.secteur == user.secteur_assigne)
    subs = subs_q.all()

    total_attribue = sum(float(s.montant_attribue or 0) for s in subs)
    total_recu = sum(float(s.montant_recu or 0) for s in subs)
    total_engage = sum(float(s.total_engage or 0) for s in subs)
    total_reste = sum(float(s.total_reste or 0) for s in subs)
    taux = 0.0
    if total_attribue > 0:
        taux = round((total_engage / total_attribue) * 100, 1)

    alerts: List[Dict[str, Any]] = []
    for s in subs:
        recu = float(s.montant_recu or 0)
        reel_lignes = float(s.total_reel_lignes or 0)
        engage = float(s.total_engage or 0)
        reste = float(s.total_reste or 0)

        if recu > 0 and reel_lignes == 0:
            alerts.append({
                "level": "danger",
                "text": f"{s.nom} : reçu {recu:.2f}€ mais lignes réel = 0€ (ventilation manquante).",
                "url": _safe("main.subvention_pilotage", subvention_id=s.id),
            })
        if reel_lignes > 0 and engage > reel_lignes:
            alerts.append({
                "level": "danger",
                "text": f"{s.nom} : engagé {engage:.2f}€ > lignes réel {reel_lignes:.2f}€ (dépassement).",
                "url": _safe("main.subvention_pilotage", subvention_id=s.id),
            })
        if float(s.montant_attribue or 0) > 0:
            pct = (engage / float(s.montant_attribue or 0)) * 100
            if pct >= 80:
                alerts.append({
                    "level": "warning",
                    "text": f"{s.nom} : {pct:.0f}% consommé (reste {reste:.2f}€).",
                    "url": _safe("main.subvention_pilotage", subvention_id=s.id),
                })

    session_date_expr = _session_effective_date_expr()
    sessions_q = SessionActivite.query.filter_by(is_deleted=False)
    pres_q = PresenceActivite.query.join(SessionActivite)
    if not has_scope_all:
        sessions_q = sessions_q.filter(SessionActivite.secteur == user.secteur_assigne)
        pres_q = pres_q.filter(SessionActivite.secteur == user.secteur_assigne)

    sessions_recent = (
        sessions_q
        .filter(session_date_expr.isnot(None))
        .filter(session_date_expr >= since_date)
        .filter(session_date_expr <= until_date)
        .count()
    )
    uniques_recent = (
        pres_q.join(Participant)
        .filter(session_date_expr.isnot(None))
        .filter(session_date_expr >= since_date)
        .filter(session_date_expr <= until_date)
        .with_entities(Participant.id)
        .distinct()
        .count()
    )

    months = _last_n_months(6)
    month_labels = [f"{y}-{m:02d}" for (y, m) in months]

    dep_q = Depense.query.filter_by(est_supprimee=False)
    if not has_scope_all:
        dep_q = (
            dep_q.join(LigneBudget)
            .join(Subvention, LigneBudget.subvention_id == Subvention.id)
            .filter(Subvention.secteur == user.secteur_assigne)
        )
    dep_rows = dep_q.with_entities(Depense.montant, Depense.date_paiement, Depense.created_at).all()

    dep_by_month = {k: 0.0 for k in month_labels}
    for montant, date_paiement, created_at in dep_rows:
        d = date_paiement or (created_at.date() if created_at else None)
        if not d:
            continue
        mk = _month_key(d)
        if mk in dep_by_month:
            dep_by_month[mk] += float(montant or 0)

    sess_rows = sessions_q.with_entities(session_date_expr).all()
    sess_by_month = {k: 0 for k in month_labels}
    for (session_date,) in sess_rows:
        if not session_date:
            continue
        mk = _month_key(session_date)
        if mk in sess_by_month:
            sess_by_month[mk] += 1

    pub_counts = {"H": 0, "S": 0, "B": 0, "A": 0, "P": 0, "?": 0}
    gender_counts = {"Femmes": 0, "Hommes": 0, "Autre": 0, "Non renseigné": 0}
    age_counts = {label: 0 for (label, _, _) in AGE_BUCKETS}
    unknown_age_count = 0
    city_counts: Dict[str, int] = {}
    quartier_counts: Dict[tuple[str, str], int] = {}

    participant_rows = (
        pres_q.join(Participant)
        .outerjoin(Quartier, Participant.quartier_id == Quartier.id)
        .filter(session_date_expr.isnot(None))
        .filter(session_date_expr >= since_date)
        .filter(session_date_expr <= until_date)
        .with_entities(
            Participant.id,
            Participant.type_public,
            Participant.genre,
            Participant.date_naissance,
            Participant.ville,
            Quartier.ville,
            Quartier.nom,
        )
        .distinct()
        .all()
    )

    for _pid, tp, genre, dob, participant_ville, quartier_ville, quartier_nom in participant_rows:
        public_key = (tp or "?").strip().upper()
        if public_key not in pub_counts:
            public_key = "?"
        pub_counts[public_key] += 1

        gender_key = _normalize_gender(genre)
        gender_counts[gender_key] = gender_counts.get(gender_key, 0) + 1

        age = _compute_age(dob, until_date)
        age_label = _age_bucket(age)
        if age_label:
            age_counts[age_label] += 1
        else:
            unknown_age_count += 1

        city_label = _clean_city(quartier_ville, participant_ville)
        quartier_label = _clean_quartier(quartier_nom)
        city_counts[city_label] = city_counts.get(city_label, 0) + 1
        quartier_counts[(city_label, quartier_label)] = quartier_counts.get((city_label, quartier_label), 0) + 1

    inner_city_order = [
        city for city, _count in sorted(city_counts.items(), key=lambda item: (-item[1], item[0].lower()))
    ]
    city_index = {city: idx for idx, city in enumerate(inner_city_order)}
    outer_segments = sorted(
        quartier_counts.items(),
        key=lambda item: (city_index.get(item[0][0], 9999), -item[1], item[0][1].lower()),
    )

    charts = {
        "budget": {
            "labels": ["Engagé", "Disponible"],
            "values": [round(total_engage, 2), round(max(total_attribue - total_engage, 0.0), 2)],
        },
        "depenses": {
            "labels": month_labels,
            "values": [round(dep_by_month[k], 2) for k in month_labels],
        },
        "sessions": {
            "labels": month_labels,
            "values": [sess_by_month[k] for k in month_labels],
        },
        "public": {
            "labels": ["Habitants", "Salariés", "Bénévoles", "Administrateurs", "Partenaires", "Autres"],
            "values": [pub_counts["H"], pub_counts["S"], pub_counts["B"], pub_counts["A"], pub_counts["P"], pub_counts["?"]],
        },
        "gender": {
            "labels": ["Femmes", "Hommes", "Autre", "Non renseigné"],
            "values": [
                gender_counts["Femmes"],
                gender_counts["Hommes"],
                gender_counts["Autre"],
                gender_counts["Non renseigné"],
            ],
            "money": False,
        },
        "ages": {
            "labels": [label for (label, _, _) in AGE_BUCKETS],
            "values": [age_counts[label] for (label, _, _) in AGE_BUCKETS],
            "unknown": unknown_age_count,
            "money": False,
        },
        "locations": {
            "inner_labels": inner_city_order,
            "inner_values": [city_counts[city] for city in inner_city_order],
            "outer_labels": [quartier for (_city, quartier), _count in outer_segments],
            "outer_values": [count for (_key, count) in outer_segments],
            "outer_parents": [city_index.get(city, 0) for (city, _quartier), _count in outer_segments],
            "outer_city_labels": [city for (city, _quartier), _count in outer_segments],
            "money": False,
        },
        "budget_donut": {
            "labels": ["Engagé", "Disponible"],
            "values": [round(total_engage, 2), round(max(total_attribue - total_engage, 0.0), 2)],
        },
        "depenses_bar": {
            "labels": month_labels,
            "values": [round(dep_by_month[k], 2) for k in month_labels],
        },
        "sessions_line": {
            "labels": month_labels,
            "values": [sess_by_month[k] for k in month_labels],
        },
        "public_pie": {
            "labels": ["Habitants", "Salariés", "Bénévoles", "Administrateurs", "Partenaires", "Autres"],
            "values": [pub_counts["H"], pub_counts["S"], pub_counts["B"], pub_counts["A"], pub_counts["P"], pub_counts["?"]],
        },
    }

    recent_depenses = dep_q.order_by(Depense.created_at.desc()).limit(6).all()
    recent_sessions = (
        sessions_q
        .order_by(session_date_expr.desc(), SessionActivite.id.desc())
        .limit(6)
        .all()
    )
    recent_participants_q = Participant.query
    if not has_scope_all:
        recent_participants_q = recent_participants_q.filter(Participant.created_secteur == user.secteur_assigne)
    recent_participants = recent_participants_q.order_by(Participant.created_at.desc()).limit(6).all()

    shortcuts = [
        {"label": "Nouvelle dépense", "url": _safe("budget.depense_new"), "icon": "➕"},
        {"label": "Nouvelle session", "url": _safe("activite.index"), "icon": "📅"},
        {"label": "Participants", "url": _safe("activite.participants", fallback=_safe("activite.index")), "icon": "👥"},
        {"label": "Inventaire", "url": _safe("inventaire_materiel.list_items"), "icon": "📦"},
        {"label": "Données activités", "url": _safe("statsimpact.dashboard"), "icon": "📊"},
        {"label": "Stats et bilans", "url": _safe("main.stats_bilans", fallback=_safe("main.dashboard")), "icon": "🧾"},
    ]

    return {
        "mode": "global" if has_scope_all else "secteur",
        "days": effective_days,
        "budget_year": budget_year,
        "period_key": current_period_key,
        "period_label": period_label,
        "activity_start": since_date,
        "activity_end": until_date,
        "kpis": {
            "attribue": round(total_attribue, 2),
            "recu": round(total_recu, 2),
            "engage": round(total_engage, 2),
            "reste": round(total_reste, 2),
            "taux": taux,
            "sessions": sessions_recent,
            "uniques": uniques_recent,
        },
        "alerts": alerts[:12],
        "shortcuts": shortcuts,
        "recents": {
            "depenses": recent_depenses,
            "sessions": recent_sessions,
            "participants": recent_participants,
        },
        "charts": charts,
    }
