import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

TEST_DB_URL = os.environ.get("DOCINT_TEST_DATABASE_URL", "postgresql+psycopg://docint:docint@localhost:5432/docint")


def _unavailable(reason: str):
    """Skip locally; in CI set DOCINT_REQUIRE_DB=1 so a missing database fails the run instead of hiding it."""
    if os.environ.get("DOCINT_REQUIRE_DB"):
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_DB_URL)
    try:
        with eng.connect() as c:
            c.execute(text("select 1"))
            has_alembic = c.execute(text("select to_regclass('public.invoices')")).scalar()
    except Exception as exc:  # pragma: no cover - environment dependent
        _unavailable(f"PostgreSQL not reachable: {exc}")
    if not has_alembic:
        _unavailable("Run `alembic upgrade head` against the test database first")
    return eng


@pytest.fixture
def db(engine):
    """Each test runs in a transaction that is rolled back."""
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    trans.rollback()
    conn.close()
