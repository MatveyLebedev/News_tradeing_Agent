"""
Shared fixtures for agent tool integration tests.
All fixtures auto-discover usable records from the real DB at test runtime.
"""
import pytest
import pytz

from news_agent.agent_config import AgentConfig
from news_agent.database.db_manager import DBManager

EASTERN = pytz.timezone("America/New_York")


@pytest.fixture(scope="session")
def db_manager():
    mgr = DBManager(AgentConfig.get_db_config())
    yield mgr
    mgr.close_all_connections()


@pytest.fixture(scope="session")
def known_ticker(db_manager):
    """Return the first ticker that has a historical_data_* table."""
    with db_manager.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name LIKE 'historical_data_%' LIMIT 1"
            )
            row = cur.fetchone()
    if not row:
        pytest.skip("No historical_data_* tables found in DB")
    return row[0].replace("historical_data_", "").upper()


@pytest.fixture(scope="session")
def past_timestamp(db_manager, known_ticker):
    """Latest timestamp available for known_ticker (guaranteed to be in the past)."""
    table = f"historical_data_{known_ticker.lower()}"
    with db_manager.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT MAX(timestamp) FROM {table}")
            row = cur.fetchone()
    if not row or row[0] is None:
        pytest.skip(f"No rows in {table}")
    ts = row[0]
    return EASTERN.localize(ts) if ts.tzinfo is None else ts.astimezone(EASTERN)


@pytest.fixture(scope="session")
def known_news_id(db_manager):
    """ID of the most recent news article in the DB."""
    try:
        with db_manager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM news ORDER BY published DESC LIMIT 1")
                row = cur.fetchone()
        if not row:
            pytest.skip("No rows in news table")
        return row[0]
    except Exception:
        pytest.skip("news table not accessible")


@pytest.fixture(scope="session")
def neo4j_manager():
    try:
        from graph_db.neo4j_manager import Neo4jManager
        mgr = Neo4jManager()
        mgr.query("RETURN 1")
        return mgr
    except Exception as e:
        pytest.skip(f"Neo4j not available: {e}")
