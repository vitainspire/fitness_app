"""Consistent error shape across every endpoint.

REQUIREMENTS → API Contract → Conventions:
    { "error": { "code": "...", "message": "..." } }
429 carries Retry-After; 401 means re-authenticate; 403 means revoked or forbidden.
"""
from __future__ import annotations

from flask import Flask, jsonify


class ApiError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None,
                 status: int | None = None, retry_after: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status:
            self.status = status
        self.retry_after = retry_after


class ValidationError(ApiError):
    status, code = 422, "validation_failed"


class Unauthorized(ApiError):
    """401 — credentials absent, malformed, or expired. Re-authenticate."""
    status, code = 401, "unauthorized"


class Revoked(ApiError):
    """403 — the token was explicitly revoked, or the session was killed."""
    status, code = 403, "revoked"


class Forbidden(ApiError):
    status, code = 403, "forbidden"


class NotFound(ApiError):
    status, code = 404, "not_found"


class Conflict(ApiError):
    status, code = 409, "conflict"


class RateLimited(ApiError):
    status, code = 429, "rate_limited"


class QuotaExhausted(ApiError):
    """A user-facing exhausted state — never a silent failure (5.5)."""
    status, code = 429, "quota_exhausted"


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def _handle_api_error(exc: ApiError):
        body = {"error": {"code": exc.code, "message": exc.message}}
        response = jsonify(body)
        response.status_code = exc.status
        if exc.retry_after is not None:
            response.headers["Retry-After"] = str(exc.retry_after)
        return response

    @app.errorhandler(404)
    def _handle_404(_):
        return jsonify({"error": {"code": "not_found", "message": "No such endpoint"}}), 404

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception):
        app.logger.exception("Unhandled exception", exc_info=exc)
        # Never leak internals to a mobile client.
        return jsonify({
            "error": {"code": "internal_error", "message": "Something went wrong"}
        }), 500
