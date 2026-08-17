"""Module 1 HTTP surface. Paths match REQUIREMENTS → API Contract."""
from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request

from app.auth import service
from app.gateway.middleware import enforce_preauth_rate_limits, require_auth
from app.platform.db import session_scope
from app.platform.errors import ValidationError

bp = Blueprint("auth", __name__, url_prefix="/v1/auth")


def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("Expected a JSON object body.")
    return data


def _device_id() -> str:
    device = request.headers.get("X-Device-Id", "").strip()
    if not device:
        # 1.4 depends on a stable device id; refusing early is clearer than
        # silently binding the session to "unknown-device".
        raise ValidationError("X-Device-Id header is required.")
    return device


def _pair_response(pair, *, status: int = 200, **extra):
    """Build the body first, then serialise.

    Do NOT jsonify and then mutate `response.json` — that property returns a
    parsed *copy*, so the mutation is silently discarded and the field never
    reaches the client. Any extra fields belong in this dict.
    """
    body = {
        "access_token": pair.access_token,
        "refresh_token": pair.refresh_token,
        "access_expires_at": pair.access_expires_at.isoformat(),
        "refresh_expires_at": pair.refresh_expires_at.isoformat(),
    }
    body.update(extra)
    return jsonify(body), status


@bp.post("/signup")
def signup():
    enforce_preauth_rate_limits()
    data = _body()
    cfg = current_app.config["APP_CONFIG"]
    with session_scope() as session:
        user, pair, verify_token = service.signup(
            session, cfg,
            email=data.get("email", ""),
            password=data.get("password", ""),
            device_id=_device_id(),
        )
        # TODO(Module 8): hand `verify_token` to the mailer. Logged in dev only.
        if not cfg.is_production:
            current_app.logger.info("email verification token: %s", verify_token)
        return _pair_response(pair, status=201, user_id=user.id)


@bp.post("/login")
def login():
    enforce_preauth_rate_limits()
    data = _body()
    cfg = current_app.config["APP_CONFIG"]
    with session_scope() as session:
        _, pair = service.login(
            session, cfg,
            email=data.get("email", ""),
            password=data.get("password", ""),
            device_id=_device_id(),
        )
        return _pair_response(pair)


@bp.post("/refresh")
def refresh():
    """Silent renewal (11.1). Sliding expiry means an active user never sees a
    login screen again after signup (1.4)."""
    data = _body()
    raw = data.get("refresh_token", "").strip()
    if not raw:
        raise ValidationError("refresh_token is required.")
    cfg = current_app.config["APP_CONFIG"]
    with session_scope() as session:
        _, pair = service.refresh(session, cfg, raw, _device_id())
        return _pair_response(pair)


@bp.post("/logout")
@require_auth
def logout():
    data = request.get_json(silent=True) or {}
    with session_scope() as session:
        service.logout(session, g.access_claims, data.get("refresh_token"))
    return jsonify({"status": "signed_out"})


@bp.post("/verify-email")
def verify_email():
    data = _body()
    token = data.get("token", "").strip()
    if not token:
        raise ValidationError("token is required.")
    with session_scope() as session:
        service.verify_email(session, token)
    return jsonify({"status": "verified"})


@bp.post("/password-reset/request")
def password_reset_request():
    enforce_preauth_rate_limits()
    data = _body()
    cfg = current_app.config["APP_CONFIG"]
    with session_scope() as session:
        raw = service.request_password_reset(session, data.get("email", ""))
        if raw and not cfg.is_production:
            current_app.logger.info("password reset token: %s", raw)
    # Identical response either way — otherwise this is an enumeration oracle (1.9).
    return jsonify({"status": "ok", "message": service.RESET_ACK})


@bp.post("/password-reset/confirm")
def password_reset_confirm():
    enforce_preauth_rate_limits()
    data = _body()
    cfg = current_app.config["APP_CONFIG"]
    with session_scope() as session:
        service.confirm_password_reset(
            session, cfg,
            raw_token=data.get("token", "").strip(),
            new_password=data.get("password", ""),
        )
    return jsonify({"status": "password_updated"})


account_bp = Blueprint("account", __name__, url_prefix="/v1")


@account_bp.delete("/account")
@require_auth
def delete_account():
    with session_scope() as session:
        service.delete_account(session, g.user_id)
    return jsonify({"status": "deletion_scheduled"})
