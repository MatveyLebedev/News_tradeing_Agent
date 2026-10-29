"""
Regression: one failed price query must not blank — or tombstone — the rest of the cycle.

2026-10-29: `_query_moves` queried a missing `historical_data_<ticker>` table on the shared
non-autocommit connection and swallowed the error without a rollback. The transaction stayed
aborted, every later query in the `update_price_movements` cycle failed too and came back {},
and edges older than the tombstone age were marked `move_unavailable = true` permanently.

The Postgres tests use TEMP tables only (nothing persists) and self-skip without a DB; the
fake-connection tests below cover the same contract in CI.
"""
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extensions as ext
import pytest

import graph_db.event_updater as eu

PUB = datetime(2026, 10, 20, 10, 0)


@pytest.fixture
def pg(db):
    conn = psycopg2.connect(**db.db_config)
    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE historical_data_zzmovetest "
                    "(timestamp timestamp PRIMARY KEY, close double precision)")
        t0 = datetime(2026, 10, 20, 9, 30)
        cur.executemany("INSERT INTO historical_data_zzmovetest VALUES (%s, %s)",
                        [(t0 + timedelta(minutes=i), 100 + i * 0.01) for i in range(2000)])
        cur.execute("CREATE TEMP TABLE historical_data_zzbroken (timestamp timestamp)")
    conn.commit()
    yield conn
    conn.close()


def test_missing_table_leaves_connection_usable(pg):
    assert eu._query_moves("ZZNOTATABLE", PUB, pg) == {}
    assert pg.get_transaction_status() != ext.TRANSACTION_STATUS_INERROR
    moves = eu._query_moves("ZZMOVETEST", PUB, pg)
    assert set(moves) == set(eu.MOVE_WINDOWS)


def test_failed_query_returns_none_and_rolls_back(pg):
    assert eu._query_moves("ZZBROKEN", PUB, pg) is None  # no `close` column → SQL error
    assert pg.get_transaction_status() != ext.TRANSACTION_STATUS_INERROR
    assert set(eu._query_moves("ZZMOVETEST", PUB, pg)) == set(eu.MOVE_WINDOWS)


def test_unsafe_ticker_is_not_interpolated(pg):
    assert eu._query_moves("^GSPC", PUB, pg) == {}
    assert eu._query_moves("X; DROP TABLE y", PUB, pg) == {}
    assert pg.get_transaction_status() != ext.TRANSACTION_STATUS_INERROR


# ── no-DB: a fake connection with Postgres's abort-until-rollback semantics ──────────────

class _AbortingConn:
    """Every statement fails once one has failed, until rollback() — like a real txn."""

    def __init__(self, fail_on: str):
        self.fail_on, self.aborted, self.rollbacks = fail_on, False, 0

    def cursor(self):
        return _AbortingCursor(self)

    def rollback(self):
        self.aborted, self.rollbacks = False, self.rollbacks + 1

    def commit(self):
        self.aborted = False


class _AbortingCursor:
    def __init__(self, conn):
        self.conn, self.sql = conn, ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if self.conn.aborted:
            raise psycopg2.errors.InFailedSqlTransaction("current transaction is aborted")
        if self.conn.fail_on in sql:
            self.conn.aborted = True
            raise psycopg2.errors.QueryCanceled("canceling statement due to statement timeout")
        self.sql, self.params = sql, params

    def fetchone(self):
        if "to_regclass" in self.sql:
            return ("t",)
        return (self.params[0], 100.0 + (self.params[0] - PUB).total_seconds() / 60)


def test_query_error_rolls_back_so_next_ticker_still_fills():
    conn = _AbortingConn(fail_on="historical_data_bad ")
    assert eu._query_moves("BAD", PUB, conn) is None
    assert conn.rollbacks == 1
    assert set(eu._query_moves("GOOD", PUB, conn)) == set(eu.MOVE_WINDOWS)


class _RecordingNeo4j:
    def __init__(self, affects, sourced):
        self.reads = [affects, sourced]
        self.writes = []

    def query(self, cypher, params=None):
        if cypher.lstrip().startswith("MATCH (e:Event)-[r:"):
            return self.reads.pop(0)
        self.writes.append(cypher)
        return []


@pytest.mark.parametrize("result, tombstoned", [(None, False), ({}, True)])
def test_tombstone_only_on_a_clean_empty_answer(monkeypatch, result, tombstoned):
    old = (datetime.now() - timedelta(days=eu.MOVES_TOMBSTONE_AGE_DAYS + 30)).isoformat()
    neo = _RecordingNeo4j(
        affects=[{"eid": "e1", "ticker": "AMD", "pub": old}],
        sourced=[{"eid": "e1", "nid": 1, "pub": old, "tickers": ["AMD"]}],
    )
    monkeypatch.setattr(eu, "_query_moves", lambda *a: result)
    eu.update_price_movements(neo, _AbortingConn(fail_on="\0"), batch=10)
    assert len(neo.writes) == 2
    for cypher in neo.writes:
        assert "move_checked_at" in cypher
        assert ("move_unavailable" in cypher) is tombstoned
