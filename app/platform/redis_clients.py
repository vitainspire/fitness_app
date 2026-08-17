"""Two Redis instances, deliberately (STRATEGY §6).

  durable  -> noeviction. Celery queues, revocation denylist, tokens_valid_after,
              rate-limit counters. Rate limits carry short TTLs so they expire on
              their own and never need eviction.
  cache    -> allkeys-lru. Curated video links only. A miss falls through to
              PostgreSQL and costs nothing.

The split that matters is evictable vs. durable. Nothing correctness-critical may
share an instance with an evictable cache, and eviction policy is per-instance —
which is exactly why the cache is physically separate.
"""
from __future__ import annotations

import redis

_durable: redis.Redis | None = None
_cache: redis.Redis | None = None


def init_redis(durable_url: str, cache_url: str, *, allow_fallback: bool = False) -> None:
    """Connect both instances.

    `allow_fallback` is a DEVELOPMENT convenience only: if a local Redis is not
    running, fall back to an in-process fake so the app can be started without
    Docker. It is never permitted in production — losing the denylist or the
    Celery queue silently is exactly the failure the durable instance exists to
    prevent, and an in-process store also disappears on every worker restart.
    """
    global _durable, _cache

    def _connect(url: str, label: str) -> redis.Redis:
        client = redis.Redis.from_url(url, decode_responses=True)
        try:
            client.ping()
            return client
        except redis.RedisError:
            if not allow_fallback:
                raise
            import logging
            import fakeredis
            logging.getLogger(__name__).warning(
                "Redis unavailable at %s — using an IN-MEMORY fake for %s. "
                "Development only; data is lost on restart and not shared "
                "between processes.", url, label,
            )
            return fakeredis.FakeRedis(decode_responses=True)

    _durable = _connect(durable_url, "durable")
    _cache = _connect(cache_url, "cache")


def durable() -> redis.Redis:
    if _durable is None:
        raise RuntimeError("init_redis() must be called before durable()")
    return _durable


def cache() -> redis.Redis:
    if _cache is None:
        raise RuntimeError("init_redis() must be called before cache()")
    return _cache


# --- Key shapes (REQUIREMENTS → Data Model → Redis key shapes) -------------

def key_revoked_jwt(jti: str) -> str:
    """Present = revoked. TTL set to the token's natural expiry."""
    return f"revoked:jwt:{jti}"


def key_tokens_valid_after(user_id: str) -> str:
    """Any access token issued before this timestamp is invalid (1.6)."""
    return f"user:{user_id}:tokens_valid_after"


def key_rl_user(user_id: str) -> str:
    return f"rl:user:{user_id}"


def key_rl_device(device_id: str) -> str:
    return f"rl:device:{device_id}"


def key_rl_ip(ip: str) -> str:
    return f"rl:ip:{ip}"


def key_login_failures(user_id: str) -> str:
    """Per-account failed-login counter (1.2). IP and device keys are
    attacker-controlled; this one is not."""
    return f"login:fail:{user_id}"
