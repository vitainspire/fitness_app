"""Token issuance, rotation, and revocation.

The three rules that are easy to get wrong and are enforced here:

  1.3  Every access token carries a `jti`. Without it the denylist has no key and
       logout silently revokes nothing.
  1.4  Grace-period reuse is bound to the DEVICE only. IP is never consulted.
  1.4  Sliding expiry with a 180-day absolute cap carried on `chain_started_at`.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import RefreshToken, User
from app.platform import redis_clients as rc
from app.platform.config import Config
from app.platform.errors import Revoked, Unauthorized


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(raw: str) -> str:
    """SHA-256 is correct here: these are high-entropy random tokens, not
    passwords. A slow KDF would add latency on the hot refresh path for no gain."""
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


# --- Access tokens ---------------------------------------------------------

def issue_access_token(cfg: Config, user_id: str) -> tuple[str, datetime, str]:
    issued = now_utc()
    expires = issued + cfg.access_token_ttl
    jti = str(uuid.uuid4())
    payload = {
        "sub": user_id,
        "jti": jti,          # 1.3 — required for revocation to function
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
    }
    token = jwt.encode(
        payload,
        cfg.jwt_keys[cfg.jwt_current_kid],
        algorithm="HS256",
        headers={"kid": cfg.jwt_current_kid},
    )
    return token, expires, jti


def decode_access_token(cfg: Config, token: str) -> dict:
    """Verify signature and expiry. Accepts any configured `kid` so a key can be
    retired without invalidating live sessions (1.3)."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise Unauthorized("Malformed token") from exc

    kid = header.get("kid")
    secret = cfg.jwt_keys.get(kid) if kid else None
    if secret is None:
        raise Unauthorized("Unknown signing key")

    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Access token expired") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid token") from exc

    if not claims.get("jti"):
        # A token without a jti cannot be revoked, so it must not be trusted.
        raise Unauthorized("Token missing jti")
    return claims


def assert_not_revoked(claims: dict) -> None:
    """2.2 — one Redis round trip covering both revocation conditions."""
    r = rc.durable()
    jti, user_id, issued_at = claims["jti"], claims["sub"], claims["iat"]

    pipe = r.pipeline()
    pipe.exists(rc.key_revoked_jwt(jti))
    pipe.get(rc.key_tokens_valid_after(user_id))
    denylisted, valid_after = pipe.execute()

    if denylisted:
        raise Revoked("This session was ended")

    if valid_after is not None and int(issued_at) < int(float(valid_after)):
        # Issued before a force-revocation (1.6 / 1.9) — kills every live token
        # for this user without needing to enumerate them.
        raise Revoked("This session was ended")


def denylist_access_token(claims: dict) -> None:
    """1.5 — hold the jti only until the token would have expired anyway."""
    ttl = int(claims["exp"]) - int(now_utc().timestamp())
    if ttl > 0:
        rc.durable().setex(rc.key_revoked_jwt(claims["jti"]), ttl, "1")


def set_tokens_valid_after(user_id: str, when: datetime | None = None) -> None:
    """1.6 / 1.9 — invalidate every access token issued before now.

    TTL matches the longest possible access-token lifetime; past that point no
    token issued before `when` can still be live, so the key is dead weight.
    """
    when = when or now_utc()
    rc.durable().setex(
        rc.key_tokens_valid_after(user_id),
        timedelta(hours=2),
        str(int(when.timestamp())),
    )


# --- Refresh tokens --------------------------------------------------------

def issue_refresh_token(
    session: Session,
    cfg: Config,
    user_id: str,
    device_id: str,
    *,
    chain_started_at: datetime | None = None,
) -> tuple[str, RefreshToken]:
    raw = secrets.token_urlsafe(48)
    issued = now_utc()
    row = RefreshToken(
        user_id=user_id,
        token_hash=hash_token(raw),
        device_id=device_id,
        expires_at=issued + cfg.refresh_token_ttl,
        # A brand-new login starts a new chain; a rotation inherits the original.
        chain_started_at=chain_started_at or issued,
    )
    session.add(row)
    return raw, row


def revoke_all_refresh_tokens(session: Session, user_id: str) -> int:
    """1.6 — ALL of them, not just the one that was reused. A compromise that
    leaked one token may have leaked others."""
    rows = session.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
    ).all()
    stamp = now_utc()
    for row in rows:
        row.revoked_at = stamp
    return len(rows)


class TheftDetected(Exception):
    """Raised when a refresh token is reused outside its grace window, or reused
    from a different device at any time (1.6). The caller must perform all four
    response actions — partial handling is worse than none."""

    def __init__(self, user_id: str, reason: str):
        super().__init__(reason)
        self.user_id = user_id
        self.reason = reason


def rotate_refresh_token(
    session: Session, cfg: Config, raw_token: str, device_id: str
) -> tuple[TokenPair, User]:
    """Validate, rotate, and return a fresh pair.

    Raises TheftDetected for reuse the grace window does not excuse.
    """
    token_hash = hash_token(raw_token)
    row = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if row is None:
        raise Unauthorized("Unknown refresh token")

    now = now_utc()

    if row.revoked_at is not None:
        raise TheftDetected(row.user_id, "revoked token presented")

    if row.rotated_at is not None:
        # Already rotated. Two cases, and the distinction is the whole point of
        # the grace window (1.4).
        same_device = row.device_id == device_id
        if same_device and row.in_grace(now):
            # A dropped response and a client retry. Benign — regardless of which
            # network the retry arrived on. IP is deliberately not consulted.
            fresh = session.scalar(
                select(RefreshToken)
                .where(
                    RefreshToken.user_id == row.user_id,
                    RefreshToken.device_id == device_id,
                    RefreshToken.rotated_at.is_(None),
                    RefreshToken.revoked_at.is_(None),
                )
                .order_by(RefreshToken.created_at.desc())
            )
            if fresh is not None:
                user = session.get(User, row.user_id)
                access, access_exp, _ = issue_access_token(cfg, row.user_id)
                # The client lost the previous response, so it does not hold the
                # new refresh token. Issue another rotation rather than replaying
                # a value we no longer have in plaintext.
                raw_new, new_row = issue_refresh_token(
                    session, cfg, row.user_id, device_id,
                    chain_started_at=fresh.chain_started_at,
                )
                fresh.rotated_at = now
                fresh.grace_until = now + cfg.refresh_grace
                return (
                    TokenPair(access, raw_new, access_exp, new_row.expires_at),
                    user,  # type: ignore[return-value]
                )
        reason = (
            "reused from a different device" if not same_device
            else "reused after grace period expired"
        )
        raise TheftDetected(row.user_id, reason)

    if row.expires_at <= now:
        # Ordinary inactivity expiry — not theft. 30 days idle ends the session.
        raise Unauthorized("Refresh token expired")

    if now - row.chain_started_at >= cfg.refresh_absolute_cap:
        # 180-day absolute cap (1.4). Sliding expiry alone would let a stolen
        # token be kept alive indefinitely by simply continuing to use it.
        row.revoked_at = now
        raise Unauthorized("Session expired — please sign in again")

    if row.device_id != device_id:
        raise TheftDetected(row.user_id, "presented from a different device")

    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        raise Unauthorized("Account unavailable")

    # Rotate: mark old, open the grace window, issue the new pair.
    row.rotated_at = now
    row.grace_until = now + cfg.refresh_grace
    raw_new, new_row = issue_refresh_token(
        session, cfg, user.id, device_id, chain_started_at=row.chain_started_at
    )
    access, access_exp, _ = issue_access_token(cfg, user.id)
    return TokenPair(access, raw_new, access_exp, new_row.expires_at), user
