"""Shared fixtures: a DB manager that skips integration tests when the DB is unreachable."""
import pytest

from longterm_agent.config import LongTermAgentConfig


@pytest.fixture(scope="session")
def db():
    cfg = LongTermAgentConfig()
    if not cfg.DB_CONFIG.get("host"):
        pytest.skip("DB not configured (set DB_HOST/.env)")
    try:
        from news_agent.database.db_manager import DBManager
        mgr = DBManager(db_config=cfg.get_db_config(), min_conn=1, max_conn=3)
        # sanity ping
        import psycopg2
        c = psycopg2.connect(**cfg.get_db_config())
        c.close()
        return mgr
    except Exception as e:
        pytest.skip(f"DB unreachable: {e}")


@pytest.fixture(scope="session")
def cfg():
    return LongTermAgentConfig()
