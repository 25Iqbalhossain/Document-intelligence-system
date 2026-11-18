from __future__ import annotations

import uuid
from collections.abc import Callable
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.security import RateLimiter, decode_access_token
from app.models import Document, User
from app.models.base import UserRole
from app.services.storage import LocalStorage, StorageBackend

bearer = HTTPBearer(auto_error=False)
PRIVILEGED = (UserRole.admin, UserRole.reviewer)


@lru_cache
def _limiter() -> RateLimiter:
    return RateLimiter(get_settings().redis_url)


def limiter() -> RateLimiter:
    return _limiter()


def get_storage(s: Settings = Depends(get_settings)) -> StorageBackend:
    return LocalStorage(s.storage_root)


def current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db),
) -> User:
    if cred is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", {"WWW-Authenticate": "Bearer"})
    try:
        data = decode_access_token(cred.credentials)
        user = db.get(User, uuid.UUID(data["sub"]))
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                            {"WWW-Authenticate": "Bearer"}) from None
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled or missing")
    return user


def require_role(*roles: UserRole) -> Callable[..., User]:
    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return user

    return dep


def get_document_for(db: Session, user: User, document_id: uuid.UUID) -> Document:
    """Owner or reviewer/admin only. Unauthorised access reports 404 so document ids cannot be probed."""
    doc = db.get(Document, document_id)
    if doc is None or doc.deleted_at is not None or (doc.owner_id != user.id and user.role not in PRIVILEGED):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return doc


def client_ip(request: Request) -> str:
    s = get_settings()
    if s.trust_proxy_headers:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            first_ip = xff.split(",")[0].strip()
            if first_ip:
                return first_ip
        x_real_ip = request.headers.get("x-real-ip")
        if x_real_ip and x_real_ip.strip():
            return x_real_ip.strip()
    return request.client.host if request.client else "unknown"
