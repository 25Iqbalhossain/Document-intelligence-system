from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, current_user, limiter, require_role
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.models import RefreshToken, User
from app.models.base import UserRole

router = APIRouter(tags=["auth"])
log = logging.getLogger("docint.auth")
_DUMMY_HASH = hash_password("not-a-real-password")  # constant-time-ish login for unknown emails


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshIn(BaseModel):
    refresh_token: str


class UserIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
    password: str = Field(min_length=12, max_length=256)
    full_name: str | None = None
    role: UserRole = UserRole.viewer


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    role: UserRole
    is_active: bool

    model_config = {"from_attributes": True}


def _issue(db: Session, user: User, s: Settings) -> TokenOut:
    raw, digest = new_refresh_token()
    db.add(RefreshToken(user_id=user.id, token_hash=digest,
                        expires_at=datetime.now(UTC) + timedelta(days=s.refresh_token_days)))
    db.commit()
    return TokenOut(access_token=create_access_token(user.id, user.role.value, s), refresh_token=raw)


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db), s: Settings = Depends(get_settings)):
    email = body.email.strip().lower()
    if not limiter().hit(f"login:{client_ip(request)}:{email}", s.login_rate_limit_per_min):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts, try again later")
    user = db.scalar(select(User).where(User.email == email))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not (user and ok and user.is_active):
        log.warning("login_failed", extra={"email_hash": hash_refresh_token(email)[:12]})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return _issue(db, user, s)


@router.post("/auth/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, db: Session = Depends(get_db), s: Settings = Depends(get_settings)):
    """Rotating refresh tokens: each is single-use; replaying a used one revokes every session of that user."""
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(body.refresh_token))
                    .with_for_update())
    if row is None or row.expires_at < datetime.now(UTC):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    if row.revoked_at is not None:
        for t in db.scalars(select(RefreshToken).where(RefreshToken.user_id == row.user_id,
                                                       RefreshToken.revoked_at.is_(None))):
            t.revoked_at = datetime.now(UTC)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token reuse detected; sign in again")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled")
    row.revoked_at = datetime.now(UTC)
    return _issue(db, user, s)


@router.post("/auth/logout", status_code=204)
def logout(body: RefreshIn, db: Session = Depends(get_db)) -> None:
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(body.refresh_token)))
    if row and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        db.commit()


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserIn, db: Session = Depends(get_db), _: User = Depends(require_role(UserRole.admin))):
    email = body.email.strip().lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(email=email, password_hash=hash_password(body.password), full_name=body.full_name, role=body.role)
    db.add(user)
    db.commit()
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_role(UserRole.admin))):
    return db.scalars(select(User).order_by(User.email)).all()
