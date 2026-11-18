"""Password hashing (scrypt, stdlib), JWT access tokens, opaque refresh tokens, rate limiting."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import Settings, get_settings

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                              dklen=len(base64.b64decode(dk)))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        return False


def _key(s: Settings) -> str:
    key = s.secret_key.get_secret_value()
    if not key:
        raise RuntimeError("DOCINT_SECRET_KEY is not set")
    return key


def create_access_token(user_id: uuid.UUID, role: str, settings: Settings | None = None) -> str:
    s = settings or get_settings()
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "role": role, "iat": now, "exp": now + timedelta(minutes=s.access_token_minutes),
               "typ": "access"}
    return jwt.encode(payload, _key(s), algorithm="HS256")


def decode_access_token(token: str, settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    data = jwt.decode(token, _key(s), algorithms=["HS256"], options={"require": ["exp", "sub"]})
    if data.get("typ") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return data


def new_refresh_token() -> tuple[str, str]:
    """(token handed to the client, sha256 stored in the database)."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class RateLimiter:
    """Fixed-window counter. Uses Redis when reachable (shared across workers), else per-process memory."""

    def __init__(self, redis_url: str | None = None) -> None:
        self._mem: dict[str, tuple[int, float]] = {}
        self._redis = None
        if redis_url:
            try:
                import redis

                self._redis = redis.Redis.from_url(redis_url, socket_connect_timeout=0.3, socket_timeout=0.3)
            except Exception:
                self._redis = None

    def hit(self, key: str, limit: int, window_s: int = 60) -> bool:
        """Count one request; returns False when the limit is exceeded."""
        if self._redis is not None:
            try:
                k = f"rl:{key}:{int(time.time() // window_s)}"
                n = self._redis.incr(k)
                if n == 1:
                    self._redis.expire(k, window_s * 2)
                return n <= limit
            except Exception:
                pass  # Redis down: degrade to the in-process counter rather than failing open or closed
        now = time.time()
        count, start = self._mem.get(key, (0, now))
        if now - start >= window_s:
            count, start = 0, now
        count += 1
        self._mem[key] = (count, start)
        return count <= limit
