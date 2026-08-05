"""Phase 5.5 stop-breach alerting: the alert path, and its blast radius when it fails.

Both tests exist because of one production failure (2026-08-05). `scan_open_breaches`
called `notifier.enabled()` on what is a @property, so the FIRST real stop breach raised
TypeError: the operator got an error instead of the breach alert, and — because the scan
ran inside the still-uncommitted grading transaction — the whole run's grades were rolled
back and re-lost identically on every retry. Nothing here touches Telegram or a database.
"""
import inspect
import sys
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

from core.notify import TelegramNotifier
from longterm_agent import grade_results

AS_OF = date(2026, 7, 1)
TODAY = date(2026, 7, 20)

CFG = SimpleNamespace(AGENT_VERSION="test", STOP_LOSS_PCT=0.10, TAKE_PROFIT_PCT=0.0,
                      TELEGRAM_BOT_TOKEN="TOKEN", TELEGRAM_CHAT_ID="111")


class FakeNotifier:
    """Mirrors the core.notify contract: `enabled` is a property, `send` is a method.

    A Mock cannot stand in here — `Mock().enabled()` succeeds, which is precisely why the
    bad call site survived; test_notifier_enabled_is_a_property pins the contract to real.
    """
    def __init__(self, *a, **kw):
        self.sent = []

    @property
    def enabled(self):
        return True

    def send(self, text):
        self.sent.append(text)
        return True


class FakeCursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.updates = []

    def execute(self, sql, params=None):
        if sql.lstrip().upper().startswith("UPDATE"):
            self.updates.append((sql, params))

    def fetchall(self):
        return self.rows


class FakeConn:
    """Records the transaction verbs in order — the ordering IS the assertion."""
    def __init__(self, cur, log):
        self._cur, self._log = cur, log

    def cursor(self):
        return self._cur

    def commit(self):
        self._log.append("commit")

    def rollback(self):
        self._log.append("rollback")

    def close(self):
        self._log.append("close")


def test_notifier_enabled_is_a_property_not_a_method():
    # If this ever becomes a method, every `notifier.enabled` call site must change with it.
    assert isinstance(inspect.getattr_static(TelegramNotifier, "enabled"), property)


def _breach_path(*a, **kw):
    return [(AS_OF + timedelta(days=i), px) for i, px in enumerate([98.0, 95.0, 86.0])]


def test_stop_breach_alerts_and_marks_the_row(monkeypatch):
    # 5-tuple: run_timestamp rides along so the scanner can resolve the fill the same way
    # the grader does. None here = a pre-existing row on the daily convention.
    cur = FakeCursor([(7, "NVDA", AS_OF, "LONG", None)])
    notifier = FakeNotifier()
    monkeypatch.setattr(grade_results, "resolve_entry",
                        lambda c, t, a, d: ("open_t1", AS_OF, 100.0))
    monkeypatch.setattr(grade_results, "fetch_price_path", _breach_path)

    with mock.patch("core.notify.TelegramNotifier", return_value=notifier):
        alerted = grade_results.scan_open_breaches(cur, CFG, TODAY)

    assert alerted == 1
    assert notifier.sent and "STOP BREACH NVDA LONG" in notifier.sent[0]
    # exit_alert_at is what makes the alert one-time; without it every run re-alerts
    assert any("exit_alert_at" in sql for sql, _ in cur.updates)


def test_stop_is_measured_from_the_MID_fill_not_the_daily_open(monkeypatch):
    """A midday trade's stop distance must start at the price the position actually paid.

    Resolving it with the daily convention would measure the stop from 09:30 — a bar the
    trade never touched — and on a morning gap that silently moves the trigger.
    """
    from datetime import datetime, time
    seen = {}
    decision = datetime.combine(AS_OF, time(12, 0))
    cur = FakeCursor([(7, "NVDA", AS_OF, "LONG", decision)])
    notifier = FakeNotifier()

    def fake_resolve(c, t, a, d):
        seen["decision"] = d
        return ("mid_1m", AS_OF, 100.0)

    def fake_path(c, t, a, days, include_asof_day=False):
        seen["include_asof_day"] = include_asof_day
        return _breach_path()

    monkeypatch.setattr(grade_results, "resolve_entry", fake_resolve)
    monkeypatch.setattr(grade_results, "fetch_price_path", fake_path)

    with mock.patch("core.notify.TelegramNotifier", return_value=notifier):
        alerted = grade_results.scan_open_breaches(cur, CFG, TODAY)

    assert alerted == 1
    assert seen["decision"] == decision, "the decision moment must reach resolve_entry"
    # The entry day IS the as_of day under mid_1m, so it must be inside the scanned path.
    assert seen["include_asof_day"] is True


def test_failing_breach_scan_does_not_discard_the_grading(monkeypatch):
    log = []
    monkeypatch.setattr(sys, "argv", ["grade_results"])  # argparse must not see pytest's argv
    monkeypatch.setattr(grade_results.psycopg2, "connect",
                        lambda **kw: FakeConn(FakeCursor(), log))
    monkeypatch.setattr(grade_results.oplog, "error",
                        lambda *a, **kw: log.append("oplog") or True)
    monkeypatch.setattr("graph_db.neo4j_manager.Neo4jManager",
                        mock.Mock(side_effect=RuntimeError("no graph in tests")))

    def boom(*a, **kw):
        log.append("scan")
        raise TypeError("'bool' object is not callable")

    monkeypatch.setattr(grade_results, "scan_open_breaches", boom)

    assert grade_results.main() == 0            # the run completes instead of propagating
    assert log.index("commit") < log.index("scan")   # grades are durable BEFORE alerting
    assert "oplog" in log                       # …and the scan failure is still recorded
    assert log[-1] == "close"
