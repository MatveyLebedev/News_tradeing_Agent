"""Tests for the read-only raw-Cypher tool (make_graph_query_tool) — fake driver, no network.

Covers: read execution + $as_of binding + always-rollback, write/DDL rejection, procedure-CALL
rejection (the SSRF surface), row cap, and output truncation.
"""
import json
from datetime import datetime


from longterm_agent.tools.analysis_tools import make_graph_query_tool

AS_OF = datetime(2026, 6, 22)


class FakeTx:
    def __init__(self, rows):
        self.rows = rows
        self.ran = None          # (cypher, params) of the last run, or None if never run
        self.rolled_back = False

    def run(self, cypher, params=None):
        self.ran = (cypher, params)
        return iter([dict(r) for r in self.rows])

    def rollback(self):
        self.rolled_back = True


class FakeSession:
    def __init__(self, tx):
        self.tx = tx

    def begin_transaction(self, timeout=None):
        return self.tx

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeDriver:
    def __init__(self, rows):
        self.tx = FakeTx(rows)

    def session(self, **kw):
        return FakeSession(self.tx)


class FakeNeo:
    def __init__(self, rows):
        self._driver = FakeDriver(rows)


def _tool(rows):
    neo = FakeNeo(rows)
    return make_graph_query_tool(neo, AS_OF), neo


def test_read_query_runs_binds_as_of_and_rolls_back():
    tool, neo = _tool([{"ticker": "META", "move_1d": 0.02}, {"ticker": "MSFT", "move_1d": -0.01}])
    out = json.loads(tool.invoke({"cypher":
        "MATCH (e:Event)-[r:AFFECTS]->(c:Company) WHERE e.date <= datetime($as_of) "
        "RETURN c.ticker LIMIT 5"}))
    assert out["n"] == 2 and out["rows"][0]["ticker"] == "META"
    cypher, params = neo._driver.tx.ran
    assert params == {"as_of": "2026-06-22T00:00:00"}     # as_of auto-bound for no-lookahead
    assert neo._driver.tx.rolled_back is True             # ALWAYS rolled back


def test_writes_rejected_without_touching_db():
    for q in ["MATCH (n) DETACH DELETE n", "CREATE (x:Foo)", "MATCH (n) SET n.x=1",
              "MERGE (c:Company {ticker:'X'})", "MATCH (n) REMOVE n.x", "DROP INDEX foo"]:
        tool, neo = _tool([{"a": 1}])
        out = json.loads(tool.invoke({"cypher": q}))
        assert "error" in out and "READ-ONLY" in out["error"]
        assert neo._driver.tx.ran is None                 # the write never reached the DB


def test_call_procedures_rejected():
    # CALL is blocked wholesale → removes the apoc.load SSRF / apoc.export write surface.
    tool, neo = _tool([])
    out = json.loads(tool.invoke({"cypher": "CALL apoc.load.json('http://169.254.169.254/latest/')"}))
    assert "error" in out and neo._driver.tx.ran is None


def test_row_cap_enforced():
    tool, _ = _tool([{"i": i} for i in range(200)])
    out = json.loads(tool.invoke({"cypher": "MATCH (n) RETURN n"}))
    assert out["n"] == 50                                  # capped at max_rows (default 50)


def test_output_truncated():
    tool, _ = _tool([{"blob": "x" * 5000}])
    out = tool.invoke({"cypher": "MATCH (n) RETURN n"})
    assert out.endswith("…[truncated]") and len(out) <= 2520


def test_empty_query_rejected():
    tool, neo = _tool([])
    out = json.loads(tool.invoke({"cypher": "   "}))
    assert "error" in out and neo._driver.tx.ran is None
