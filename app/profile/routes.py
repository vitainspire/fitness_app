"""HTTP surface for Modules 3, 4, 6, 13, 14."""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.gateway.middleware import require_auth
from app.platform.db import session_scope
from app.platform.errors import Forbidden, ValidationError
from app.profile import service

bp = Blueprint("core", __name__, url_prefix="/v1")


def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("Expected a JSON object body.")
    return data


@bp.get("/conditions")
def conditions():
    """Public: the client needs this to render onboarding before a profile exists."""
    with session_scope() as s:
        return jsonify({"conditions": service.list_conditions(s)})


@bp.get("/taxonomy/activities")
def activities():
    with session_scope() as s:
        return jsonify({"activities": service.list_activities(s)})


@bp.post("/profile/onboarding")
@require_auth
def onboarding():
    d = _body()
    with session_scope() as s:
        return jsonify(service.complete_onboarding(
            s, g.user_id,
            age=d.get("age"),
            goal=(d.get("goal") or "").strip(),
            experience_band=(d.get("experience_band") or "").strip(),
            condition_codes=d.get("condition_codes") or [],
            unit_preference=(d.get("unit_preference") or "km").strip(),
        ))


@bp.get("/profile")
@require_auth
def profile():
    with session_scope() as s:
        return jsonify(service.profile_summary(s, g.user_id))


@bp.get("/program")
@require_auth
def program():
    with session_scope() as s:
        return jsonify({"program": service.get_program(s, g.user_id)})


@bp.get("/program/catalogue")
@require_auth
def program_catalogue():
    with session_scope() as s:
        return jsonify({"catalogue": service.list_catalogue(s, g.user_id)})


@bp.post("/program/entries")
@require_auth
def add_program_entry():
    d = _body()
    with session_scope() as s:
        return jsonify(service.add_program_entry(
            s, g.user_id, (d.get("activity_type") or "").strip(),
            override_confirmed=bool(d.get("override_confirmed")),
        ))


@bp.delete("/program/entries/<activity_type>")
@require_auth
def remove_program_entry(activity_type: str):
    with session_scope() as s:
        return jsonify(service.remove_program_entry(s, g.user_id, activity_type))


@bp.get("/suggestions")
@require_auth
def suggestions():
    with session_scope() as s:
        return jsonify(service.suggestions_today(s, g.user_id))


@bp.post("/activities")
@require_auth
def log():
    d = _body()
    entries = d.get("entries") or [d]
    out = []
    with session_scope() as s:
        for e in entries:
            out.append(service.log_activity(
                s, g.user_id,
                activity_type=(e.get("activity_type") or "").strip(),
                amount=float(e.get("amount") or e.get("quantity") or 0),
                sets=int(e.get("sets") or 1),
                unit=(e.get("unit") or None),
                source=(e.get("source") or "manual"),
                client_entry_id=e.get("client_entry_id"),
            ))
        return jsonify({"logged": out, "streak": service.streak(s, g.user_id)}), 201


@bp.get("/dashboard/summary")
@require_auth
def dashboard():
    with session_scope() as s:
        return jsonify({
            "streak": service.streak(s, g.user_id),
            "week": service.weekly_volume(s, g.user_id),
            "recent": service.recent_logs(s, g.user_id),
            "suggestions": service.suggestions_today(s, g.user_id),
        })


@bp.get("/videos")
@require_auth
def videos():
    with session_scope() as s:
        return jsonify(service.video_library(s, g.user_id))


@bp.put("/admin/videos/<activity_type>")
@require_auth
def curate_video(activity_type: str):
    """Admin curation (7.1) — paste one watched, approved YouTube URL.

    Staff auth is still an open decision (1.7); until it lands this is
    authenticated-user only and must not ship. Guarded so it cannot.
    """
    from flask import current_app
    cfg = current_app.config["APP_CONFIG"]
    if cfg.is_production:
        raise Forbidden("Curation requires staff authentication, which is not built yet (1.7).")
    d = _body()
    with session_scope() as s_:
        return jsonify(service.set_video(
            s_, activity_type,
            url_or_id=(d.get("url") or "").strip(),
            title=(d.get("title") or "").strip() or None,
        ))
