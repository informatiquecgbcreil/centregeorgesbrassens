from __future__ import annotations

from collections import defaultdict
from datetime import date

from app.models import Evaluation, Objectif, ObjectifCompetenceMap, Participant


def _eval_rows(competence_ids: set[int], start_date: date | None = None, end_date: date | None = None):
    if not competence_ids:
        return []
    q = Evaluation.query.filter(Evaluation.competence_id.in_(competence_ids))
    if start_date:
        q = q.filter(Evaluation.date_evaluation >= start_date)
    if end_date:
        q = q.filter(Evaluation.date_evaluation <= end_date)
    return q.order_by(Evaluation.participant_id.asc(), Evaluation.competence_id.asc(), Evaluation.date_evaluation.asc(), Evaluation.id.asc()).all()


def participant_timeline(participant_id: int):
    participant = Participant.query.get_or_404(participant_id)
    events = (
        Evaluation.query
        .filter(Evaluation.participant_id == participant_id)
        .order_by(Evaluation.date_evaluation.asc(), Evaluation.id.asc())
        .all()
    )
    current_levels: dict[int, int] = {}
    for e in events:
        current_levels[e.competence_id] = e.etat
    return participant, events, current_levels


def compute_objectif_scores(projet_id: int | None = None, start_date: date | None = None, end_date: date | None = None):
    objectifs_q = Objectif.query
    if projet_id:
        objectifs_q = objectifs_q.filter(Objectif.projet_id == projet_id)
    objectifs = objectifs_q.order_by(Objectif.created_at.asc()).all()

    objectif_by_id = {o.id: o for o in objectifs}
    children = defaultdict(list)
    for obj in objectifs:
        if obj.parent_id:
            children[obj.parent_id].append(obj.id)

    maps = ObjectifCompetenceMap.query.filter(
        ObjectifCompetenceMap.objectif_id.in_(objectif_by_id.keys()),
        ObjectifCompetenceMap.actif.is_(True),
    ).all() if objectif_by_id else []

    grouped = defaultdict(list)
    comp_ids = set()
    for m in maps:
        grouped[m.objectif_id].append(m)
        comp_ids.add(m.competence_id)

    rows = _eval_rows(comp_ids, start_date=start_date, end_date=end_date)

    latest = {}
    per_pair = defaultdict(list)
    for e in rows:
        key = (e.participant_id, e.competence_id)
        latest[key] = e.etat
        per_pair[key].append(e.etat)

    comp_latest_values = defaultdict(list)
    comp_participants = defaultdict(set)
    comp_progressions = defaultdict(list)
    for (pid, cid), lv in latest.items():
        comp_latest_values[cid].append(lv)
        comp_participants[cid].add(pid)
        series = per_pair[(pid, cid)]
        if len(series) >= 2:
            comp_progressions[cid].append(series[-1] - series[0])
        else:
            comp_progressions[cid].append(0)

    part_by_obj = defaultdict(set)
    eval_count_by_obj = defaultdict(int)
    progression_by_obj = {}
    score_by_obj = {}

    for obj_id, items in grouped.items():
        numerator = 0.0
        denom = 0.0
        prog_num = 0.0
        prog_den = 0.0
        for m in items:
            w = float(m.poids or 1.0)
            denom += w
            values = comp_latest_values.get(m.competence_id, [])
            if values:
                numerator += (sum(values) / len(values)) * w
                eval_count_by_obj[obj_id] += len(values)
                for pid in comp_participants.get(m.competence_id, set()):
                    part_by_obj[obj_id].add(pid)
            pvals = comp_progressions.get(m.competence_id, [])
            if pvals:
                prog_num += (sum(pvals) / len(pvals)) * w
                prog_den += w

        score_by_obj[obj_id] = round((numerator / (denom * 3.0) * 100.0), 1) if denom > 0 else None
        progression_by_obj[obj_id] = round((prog_num / prog_den), 2) if prog_den > 0 else None

    def rollup(obj_id: int):
        if score_by_obj.get(obj_id) is not None:
            return score_by_obj[obj_id], progression_by_obj.get(obj_id)
        child_vals = [rollup(cid) for cid in children.get(obj_id, [])]
        child_scores = [v[0] for v in child_vals if v[0] is not None]
        child_progs = [v[1] for v in child_vals if v[1] is not None]
        score = round(sum(child_scores) / len(child_scores), 1) if child_scores else None
        prog = round(sum(child_progs) / len(child_progs), 2) if child_progs else None
        return score, prog

    data = []
    for obj in objectifs:
        s, prog = rollup(obj.id)
        data.append({
            "objectif": obj,
            "score": s,
            "participants": len(part_by_obj.get(obj.id, set())),
            "evaluations": eval_count_by_obj.get(obj.id, 0),
            "progression_moyenne": prog,
        })
    return data
