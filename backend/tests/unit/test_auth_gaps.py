import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock
import uuid

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
from starlette.requests import Request
from fastapi import HTTPException

# Ensure pgvector is handled if optional/mocked in test run
if "pgvector" not in sys.modules:
    import sqlalchemy.types as satypes
    class DummyVector(satypes.UserDefinedType):
        def __init__(self, *args, **kwargs):
            pass
    pgv = types.ModuleType("pgvector")
    pgv_sa = types.ModuleType("pgvector.sqlalchemy")
    pgv_sa.Vector = DummyVector
    pgv.sqlalchemy = pgv_sa
    sys.modules["pgvector"] = pgv
    sys.modules["pgvector.sqlalchemy"] = pgv_sa

from app.api.deps import client_ip
from app.core.config import Settings
from app.api.routers.auth import update_user, change_password, UserUpdateIn, ChangePasswordIn
from app.models.base import UserRole
from app.models import User
from app.core.security import hash_password, verify_password


class TestAuthGaps(unittest.TestCase):
    def test_client_ip_direct(self):
        scope = {
            "type": "http",
            "client": ("192.168.1.10", 12345),
            "headers": [],
        }
        req = Request(scope)
        self.assertEqual(client_ip(req), "192.168.1.10")

    def test_client_ip_x_forwarded_for(self):
        scope = {
            "type": "http",
            "client": ("127.0.0.1", 8000),
            "headers": [
                (b"x-forwarded-for", b"203.0.113.195, 70.41.3.18, 150.172.238.178"),
            ],
        }
        req = Request(scope)
        self.assertEqual(client_ip(req), "203.0.113.195")

    def test_client_ip_x_real_ip(self):
        scope = {
            "type": "http",
            "client": ("127.0.0.1", 8000),
            "headers": [
                (b"x-real-ip", b"198.51.100.42"),
            ],
        }
        req = Request(scope)
        self.assertEqual(client_ip(req), "198.51.100.42")

    def test_client_ip_proxy_trust_disabled(self):
        scope = {
            "type": "http",
            "client": ("127.0.0.1", 8000),
            "headers": [
                (b"x-forwarded-for", b"203.0.113.195"),
            ],
        }
        req = Request(scope)
        import app.api.deps as deps
        orig = deps.get_settings
        deps.get_settings = lambda: Settings(trust_proxy_headers=False)
        try:
            self.assertEqual(client_ip(req), "127.0.0.1")
        finally:
            deps.get_settings = orig

    def test_update_user_admin_self_deactivation_blocked(self):
        admin_id = uuid.uuid4()
        admin_user = User(id=admin_id, email="admin@example.com", password_hash="x", role=UserRole.admin, is_active=True)
        db_mock = MagicMock()
        db_mock.get.return_value = admin_user

        with self.assertRaises(HTTPException) as ctx:
            update_user(user_id=admin_id, body=UserUpdateIn(is_active=False), db=db_mock, admin_user=admin_user)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("cannot deactivate their own account", ctx.exception.detail)

    def test_update_user_admin_self_demote_blocked(self):
        admin_id = uuid.uuid4()
        admin_user = User(id=admin_id, email="admin@example.com", password_hash="x", role=UserRole.admin, is_active=True)
        db_mock = MagicMock()
        db_mock.get.return_value = admin_user

        with self.assertRaises(HTTPException) as ctx:
            update_user(user_id=admin_id, body=UserUpdateIn(role=UserRole.viewer), db=db_mock, admin_user=admin_user)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("cannot remove their own admin role", ctx.exception.detail)

    def test_update_user_deactivate_other(self):
        admin_id = uuid.uuid4()
        admin_user = User(id=admin_id, email="admin@example.com", password_hash="x", role=UserRole.admin, is_active=True)
        target_id = uuid.uuid4()
        target_user = User(id=target_id, email="user@example.com", password_hash="x", role=UserRole.viewer, is_active=True)
        db_mock = MagicMock()
        db_mock.get.return_value = target_user
        db_mock.scalars.return_value = []

        res = update_user(user_id=target_id, body=UserUpdateIn(is_active=False), db=db_mock, admin_user=admin_user)
        self.assertFalse(res.is_active)
        db_mock.commit.assert_called_once()

    def test_change_password_success_and_failures(self):
        user = User(id=uuid.uuid4(), email="u@example.com", password_hash=hash_password("OldPassword123!"), role=UserRole.viewer, is_active=True)
        db_mock = MagicMock()
        db_mock.scalars.return_value = []

        # Wrong current password
        with self.assertRaises(HTTPException) as ctx:
            change_password(body=ChangePasswordIn(current_password="WrongPassword123!", new_password="NewPassword123!"), user=user, db=db_mock)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Current password is incorrect", ctx.exception.detail)

        # Same new password
        with self.assertRaises(HTTPException) as ctx:
            change_password(body=ChangePasswordIn(current_password="OldPassword123!", new_password="OldPassword123!"), user=user, db=db_mock)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("must be different", ctx.exception.detail)

        # Successful password change
        change_password(body=ChangePasswordIn(current_password="OldPassword123!", new_password="BrandNewPassword123!"), user=user, db=db_mock)
        self.assertTrue(verify_password("BrandNewPassword123!", user.password_hash))
        db_mock.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
