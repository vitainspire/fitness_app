"""Module 1 business logic.

The pieces here that carry real weight:
  1.2   per-account lockout, because IP and device keys are attacker-controlled
  1.6   theft response performs all four actions, never a subset
  1.9   reset responses are identical whether or not the email exists
  1.10  the deletion record outlives the data it removed
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import tokens as tk
from app.auth.models import (
    DeletionRequest, DevicePushToken, EmailVerificationToken,
    PasswordResetToken, RefreshToken, User,
)
from app.auth.passwords import hash_password, validate_password, verify_password
from app.platform import redis_clients as rc
from app.platform.config import Config
from app.platform.errors import Conflict, RateLimited, Unauthorized, ValidationError
from app.profile.models import OverrideConsent

# Deliberately vague and identical for both branches (1.9).
RESET_ACK = "If an account exists for that address, a reset link has been sent."


def _normalise_email(email: str) -> str:
    return email.strip().lower()


def _hash_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode()).hexdigest()


# --- Signup ----------------------------------------------------------------

def signup(session: Session, cfg: Config, email: str, password: str,
           device_id: str) -> tuple[User, tk.TokenPair, str]:
    email = _normalise_email(email)
    if "@" not in email or len(email) < 3:
        raise ValidationError("Enter a valid email address.")
    validate_password(
        password,
        min_length=cfg.password_min_length,
        breach_check=cfg.breach_check_enabled,
    )

    user = User(email=email, password_hash=hash_password(password))
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        # The DB constraint is the real guard — two concurrent signups for the
        # same address both reach here and exactly one wins (1.1).
        raise Conflict("An account with that email already exists.") from exc

    verify_raw = _issue_email_verification(session, user.id)
    access, access_exp, _ = tk.issue_access_token(cfg, user.id)
    raw_refresh, refresh_row = tk.issue_refresh_token(session, cfg, user.id, device_id)
    pair = tk.TokenPair(access, raw_refresh, access_exp, refresh_row.expires_at)
    return user, pair, verify_raw


# --- Login -----------------------------------------------------------------

def _lockout_delay(cfg: Config, failures: int) -> int:
    """Exponential backoff, capped. Temporary and self-clearing — a permanent
    lock would be a denial of service against the real account owner (1.2)."""
    over = failures - cfg.login_lockout_threshold
    if over < 0:
        return 0
    return min(cfg.login_lockout_base_seconds * (2 ** over), 900)


def login(session: Session, cfg: Config, email: str, password: str,
          device_id: str) -> tuple[User, tk.TokenPair]:
    email = _normalise_email(email)
    user = session.scalar(select(User).where(User.email == email))

    # Per-account counter (1.2). Keyed on the account, because per-IP and
    # per-device limits are trivially bypassed by rotating both.
    fail_key = rc.key_login_failures(user.id) if user else None
    if fail_key:
        failures = int(rc.durable().get(fail_key) or 0)
        delay = _lockout_delay(cfg, failures)
        if delay:
            raise RateLimited(
                "Too many failed attempts. Please wait before trying again.",
                retry_after=delay,
            )

    if user is None or not user.is_active or not verify_password(user.password_hash, password):
        if fail_key:
            pipe = rc.durable().pipeline()
            pipe.incr(fail_key)
            pipe.expire(fail_key, timedelta(hours=1))
            pipe.execute()
        # Same message either way — do not reveal whether the address exists.
        raise Unauthorized("Email or password is incorrect.")

    rc.durable().delete(fail_key)  # resets on success (1.2)

    access, access_exp, _ = tk.issue_access_token(cfg, user.id)
    raw_refresh, refresh_row = tk.issue_refresh_token(session, cfg, user.id, device_id)
    return user, tk.TokenPair(access, raw_refresh, access_exp, refresh_row.expires_at)


# --- Refresh ---------------------------------------------------------------

def refresh(session: Session, cfg: Config, raw_refresh: str,
            device_id: str) -> tuple[User, tk.TokenPair]:
    try:
        pair, user = tk.rotate_refresh_token(session, cfg, raw_refresh, device_id)
    except tk.TheftDetected as theft:
        handle_theft(theft.user_id, theft.reason)
        raise Unauthorized(
            "Your session was ended for security reasons. Please sign in again."
        ) from theft
    return user, pair


def handle_theft(user_id: str, reason: str) -> None:
    """1.6 — all four actions, committed in their OWN transaction.

    This deliberately does not take the caller's session. The refresh request
    that triggered detection must fail with 401, and that exception unwinds the
    caller's `session_scope`, which rolls back. Writing the revocations into that
    session would mean they are silently discarded at the moment they matter most
    — leaving the attacker holding a refresh token that still rotates.

    Verified by the `theft_revokes_everything` test: without a separate
    transaction, step 11 of the smoke run returns 200 instead of 401.
    """
    from app.platform.db import session_scope

    with session_scope() as security_session:
        tk.revoke_all_refresh_tokens(security_session, user_id)          # 1
        security_session.query(DevicePushToken).filter(                  # 3
            DevicePushToken.user_id == user_id
        ).delete(synchronize_session=False)

    tk.set_tokens_valid_after(user_id)                                   # 2 (Redis)

    # 4 — must not happen silently. Wired to the alerting channel in Module 10.
    import logging
    logging.getLogger("security").critical(
        "refresh_token_theft_detected user_id=%s reason=%s", user_id, reason
    )


# --- Logout ----------------------------------------------------------------

def logout(session: Session, claims: dict, raw_refresh: str | None) -> None:
    tk.denylist_access_token(claims)                         # 1.5
    if raw_refresh:
        row = session.scalar(
            select(RefreshToken).where(
                RefreshToken.token_hash == tk.hash_token(raw_refresh)
            )
        )
        if row is not None and row.revoked_at is None:
            row.revoked_at = tk.now_utc()


# --- Email verification ----------------------------------------------------

def _issue_email_verification(session: Session, user_id: str) -> str:
    raw = secrets.token_urlsafe(32)
    session.add(EmailVerificationToken(
        user_id=user_id,
        token_hash=tk.hash_token(raw),
        expires_at=tk.now_utc() + timedelta(hours=24),
    ))
    return raw


def verify_email(session: Session, raw_token: str) -> None:
    row = session.scalar(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == tk.hash_token(raw_token)
        )
    )
    now = tk.now_utc()
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise ValidationError("This verification link is invalid or has expired.")
    row.used_at = now
    user = session.get(User, row.user_id)
    if user is not None:
        user.email_verified_at = now


# --- Password reset --------------------------------------------------------

def request_password_reset(session: Session, email: str) -> str:
    """Returns the raw token for the mailer. The HTTP layer must return the same
    acknowledgement whether or not an account exists (1.9)."""
    user = session.scalar(select(User).where(User.email == _normalise_email(email)))
    if user is None or not user.is_active:
        return ""  # caller still returns RESET_ACK
    raw = secrets.token_urlsafe(32)
    session.add(PasswordResetToken(
        user_id=user.id,
        token_hash=tk.hash_token(raw),
        expires_at=tk.now_utc() + timedelta(hours=1),
    ))
    return raw


def confirm_password_reset(session: Session, cfg: Config,
                           raw_token: str, new_password: str) -> None:
    row = session.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == tk.hash_token(raw_token)
        )
    )
    now = tk.now_utc()
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise ValidationError("This reset link is invalid or has expired.")

    validate_password(
        new_password,
        min_length=cfg.password_min_length,
        breach_check=cfg.breach_check_enabled,
    )

    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        raise ValidationError("This reset link is invalid or has expired.")

    user.password_hash = hash_password(new_password)
    row.used_at = now

    # Someone resetting because they suspect compromise must not leave the
    # attacker's session alive (1.9).
    tk.revoke_all_refresh_tokens(session, user.id)
    tk.set_tokens_valid_after(user.id)
    rc.durable().delete(rc.key_login_failures(user.id))


def change_password(session: Session, cfg: Config, user_id: str, *,
                    current_password: str, new_password: str) -> None:
    """Logged-in password change from Settings. Requires the current password
    rather than an emailed token — the user is already proven to hold a valid
    session, so this is the direct equivalent of confirm_password_reset above,
    minus the token.
    """
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise ValidationError("Account not found.")
    if not verify_password(user.password_hash, current_password):
        raise ValidationError("Current password is incorrect.")

    validate_password(
        new_password,
        min_length=cfg.password_min_length,
        breach_check=cfg.breach_check_enabled,
    )

    user.password_hash = hash_password(new_password)

    # Same treatment as a reset (1.9): changing a password is exactly the
    # moment to kill any other live session, in case the old password had
    # already leaked. This device's refresh token is revoked too — signing
    # in again with the new password is the expected next step.
    tk.revoke_all_refresh_tokens(session, user.id)
    tk.set_tokens_valid_after(user.id)
    rc.durable().delete(rc.key_login_failures(user.id))


# --- Account deletion ------------------------------------------------------

def delete_account(session: Session, user_id: str, *, pitr_days: int = 35) -> None:
    """1.10 — soft delete now, and leave a record that survives a restore."""
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        return

    now = tk.now_utc()
    user.deleted_at = now

    tk.revoke_all_refresh_tokens(session, user_id)
    tk.set_tokens_valid_after(user_id)
    session.query(DevicePushToken).filter(
        DevicePushToken.user_id == user_id
    ).delete(synchronize_session=False)

    # 14.7 — keep the override-consent evidence, strip the identity attached
    # to it. Proof the warning system worked; no health data tied to a person
    # once they're gone.
    session.query(OverrideConsent).filter(
        OverrideConsent.user_id == user_id
    ).update({OverrideConsent.user_id: None}, synchronize_session=False)

    session.add(DeletionRequest(
        user_id_hash=_hash_user_id(user_id),
        completed_at=now,
        purge_after=now + timedelta(days=pitr_days),
    ))
