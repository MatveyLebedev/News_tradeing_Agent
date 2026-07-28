"""Shared fixtures: a DB manager that skips integration tests when the DB is unreachable."""
import pathlib
import sys

import pytest

from longterm_agent.config import LongTermAgentConfig

# The fetcher package uses FLAT imports (`from config import ...`) and ships in its own
# container, so its `config` module collides with the repo-root `config.py` shim. Whichever
# is imported first wins for the whole session: run alone the fetcher's wins, run with the
# full suite the root shim does — and the root shim happens to export DB_CONFIG, so the
# import half-succeeds instead of failing loudly. Tests that guard fetcher behaviour were
# silently skipping in CI because of it. This fixture makes the resolution explicit.
_FETCHER_MODULES = ("config", "auto_news_fetcher", "daily_price_fetcher",
                    "logger", "price_fetcher", "fundamentals_fetcher")


@pytest.fixture
def fetcher_pkg():
    """Import `bloomberg_news_fetcher` with ITS `config` winning, and restore afterwards.

    Yields (auto_news_fetcher, config). Skips only when yfinance is absent (the agent venv
    does not carry it); an import failure of the package itself is a real failure and is
    left to raise.
    """
    pytest.importorskip("yfinance")
    root = str(pathlib.Path(__file__).resolve().parents[2] / "bloomberg_news_fetcher")
    saved_path, saved_mods = list(sys.path), {}
    for name in _FETCHER_MODULES:
        if name in sys.modules:
            saved_mods[name] = sys.modules.pop(name)
    sys.path.insert(0, root)
    try:
        import config as fetcher_config
        import auto_news_fetcher
        # Proves the swap took: without it every assertion below would be testing the
        # root shim and passing/skipping for the wrong reason.
        assert root in str(pathlib.Path(fetcher_config.__file__).parent), \
            f"wrong `config` module resolved: {fetcher_config.__file__}"
        yield auto_news_fetcher, fetcher_config
    finally:
        sys.path[:] = saved_path
        for name in _FETCHER_MODULES:
            sys.modules.pop(name, None)
        sys.modules.update(saved_mods)


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


@pytest.fixture(scope="session")
def neo():
    """Neo4j manager, or skip. Used by the deep-tool leakage canaries."""
    try:
        from graph_db.neo4j_manager import Neo4jManager
        mgr = Neo4jManager()
        mgr.query("RETURN 1 AS ok")  # sanity ping
    except Exception as e:
        pytest.skip(f"Neo4j unreachable: {e}")
        return
    yield mgr
    try:
        mgr.close()
    except Exception:
        pass
