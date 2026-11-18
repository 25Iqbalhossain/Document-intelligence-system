"""Create the first admin user. Password is read from DOCINT_ADMIN_PASSWORD or prompted; never passed on argv.

Usage:  DOCINT_ADMIN_PASSWORD=... python scripts/create_admin.py admin@example.com
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import select  # noqa: E402

from app.core.database import get_session_factory  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models import User  # noqa: E402
from app.models.base import UserRole  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    email = sys.argv[1].strip().lower()
    password = os.environ.get("DOCINT_ADMIN_PASSWORD") or getpass.getpass("Password (min 12 chars): ")
    if len(password) < 12:
        print("Password must be at least 12 characters")
        return 1
    with get_session_factory()() as s:
        if s.scalar(select(User.id).where(User.email == email)):
            print("User already exists")
            return 1
        s.add(User(email=email, password_hash=hash_password(password), role=UserRole.admin, full_name="Administrator"))
        s.commit()
    print(f"Admin {email} created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
