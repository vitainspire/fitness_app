"""Module 2 — auth middleware and rate limiting.

This is middleware applied at the edge, not a service. No business module imports it.

2.3 — three independent limits. Per-device and per-IP are enforced SEPARATELY on
pre-auth endpoints: a single combined key would let an attacker reset the counter
by cycling device IDs, which defeats the IP backstop entirely.
"""
from __future__ import annotations

import functools
from typing import Callable

from flask import current_app, g, request

from app.auth import tokens as tk
from app.platform import redis_clients as rc
from app.platform.errors import RateLimited, Unauthorized


def _client_ip() -> str:
    # Trust the platform's forwarded header; Railway/Nginx sets it.
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _device_id() -> str:
    """Stable per-install identifier from the client (1.4). Never an IP."""
    return request.headers.get("X-Device-Id", "").strip() or "unknown-device"


def _incr_window(key: str, limit: int, window_seconds: int) -> None:
    r = rc.durable()
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.ttl(key)
    count, ttl = pipe.execute()
    if ttl is None or ttl < 0:
        r.expire(key, window_seconds)
        ttl = window_seconds
    if count > limit:
        raise RateLimited(
            "Too many requests. Please slow down.",
            retry_after=max(int(ttl), 1),
        )


def enforce_preauth_rate_limits() -> None:
    """Signup / login / reset. Both limits must pass — they are not merged."""
    cfg = current_app.config["APP_CONFIG"]
    _incr_window(rc.key_rl_device(_device_id()), cfg.rl_device_per_hour, 3600)
    _incr_window(rc.key_rl_ip(_client_ip()), cfg.rl_ip_per_hour, 3600)


def enforce_user_rate_limit(user_id: str) -> None:
    cfg = current_app.config["APP_CONFIG"]
    _incr_window(rc.key_rl_user(user_id), cfg.rl_user_per_minute, 60)


def require_auth(fn: Callable) -> Callable:
    """Verify signature, then check revocation, then rate-limit the user.

    Order matters: an invalid token must not consume a rate-limit slot keyed on a
    user id we have not yet authenticated.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise Unauthorized("Missing bearer token")
        raw = header[7:].strip()

        cfg = current_app.config["APP_CONFIG"]
        claims = tk.decode_access_token(cfg, raw)   # 2.1 stateless verification
        tk.assert_not_revoked(claims)               # 2.2 single Redis round trip

        g.user_id = claims["sub"]
        g.access_claims = claims
        g.device_id = _device_id()

        enforce_user_rate_limit(g.user_id)
        return fn(*args, **kwargs)

    return wrapper
