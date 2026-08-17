"""Password hashing and policy (1.1).

Policy: minimum length, reject known-breached passwords. Length beats composition
rules — no mandated symbol classes.
"""
from __future__ import annotations

import hashlib

import requests
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.platform.errors import ValidationError

_hasher = PasswordHasher()

_PWNED_RANGE_URL = "https://api.pwnedpasswords.com/range/{prefix}"


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(stored_hash: str, plain: str) -> bool:
    try:
        return _hasher.verify(stored_hash, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False


def is_breached(plain: str, *, timeout: float = 2.0) -> bool:
    """k-anonymity breach check: only the first 5 hex characters of the SHA-1
    hash leave this process, so the password itself is never transmitted.

    Fails OPEN on any network problem. A breach lookup outage must not stop
    people signing up — the length floor still applies.
    """
    digest = hashlib.sha1(plain.encode()).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    try:
        resp = requests.get(_PWNED_RANGE_URL.format(prefix=prefix), timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        return False
    return any(line.split(":")[0] == suffix for line in resp.text.splitlines())


def validate_password(plain: str, *, min_length: int, breach_check: bool) -> None:
    if len(plain) < min_length:
        raise ValidationError(
            f"Password must be at least {min_length} characters."
        )
    if breach_check and is_breached(plain):
        raise ValidationError(
            "This password has appeared in a known data breach. Please choose another."
        )
