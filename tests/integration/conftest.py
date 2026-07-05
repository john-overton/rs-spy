"""Fixtures for Postgres-backed integration tests.

These need a real Postgres (the runs-store uses JSONB/BYTEA/UUID/ENUM/COPY, so a
sqlite shim would test different SQL). They spin an ephemeral postgres:16 via
testcontainers and AUTO-SKIP when Docker/testcontainers is unavailable, so the
hermetic unit suite (`python -m pytest -q`) stays green everywhere.
"""
import os

import pytest

from rs_spy.store.connection import connect_pg
from rs_spy.store.schema import init_schema

# Ryuk (testcontainers' reaper container) hangs on container startup under this
# machine's Docker Desktop; disable it. The ephemeral PG container is torn down
# by the `with` block in the pg_url fixture regardless.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")


def _pg_url_for_psycopg(container) -> str:
    """testcontainers returns a SQLAlchemy-style URL (postgresql+psycopg://...)
    that psycopg.connect can't parse; strip the driver suffix."""
    return container.get_connection_url().replace("+psycopg2", "").replace("+psycopg", "")


def _docker_available() -> bool:
    try:
        import docker  # noqa: F401  (testcontainers dep)

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def pg_url():
    """Session-scoped ephemeral Postgres container URL, or skip."""
    testcontainers = pytest.importorskip("testcontainers.postgres")
    if not _docker_available():
        pytest.skip("Docker not available; skipping Postgres integration tests")
    with testcontainers.PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
        yield _pg_url_for_psycopg(pg)


@pytest.fixture
def pg_conn(pg_url):
    """A connection with a fresh schema; tables truncated after each test so
    tests don't see each other's rows (the container is reused across tests)."""
    conn = connect_pg(pg_url)
    init_schema(conn)
    yield conn
    with conn.cursor() as cur:
        cur.execute("TRUNCATE runs, trades, equity_curves RESTART IDENTITY CASCADE")
    conn.commit()
    conn.close()
