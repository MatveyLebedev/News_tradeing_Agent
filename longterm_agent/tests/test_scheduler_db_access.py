"""The scheduler's DB access, checked without a DB.

`Ops` reaches Postgres from job methods that fire once a day (grading, broker) or once a
week (batch, Monday cohort), so a wrong call there is invisible until the schedule reaches
it. That is exactly what happened: the broker steps called `self.db.get_cursor()`, a method
DBManager never had, and the miss surfaced only as a daily
`AttributeError: 'DBManager' object has no attribute 'get_cursor'` in Telegram.

Two guards, both static:
  * every attribute the module touches on `self.db` exists on DBManager;
  * `Ops._cursor` commits, since the broker modules write through the cursor they are given
    and never commit themselves — an uncommitted broker write is silently rolled back when
    the connection closes, which looks like a broker that submitted nothing.
"""
import ast
import inspect
import pathlib
import textwrap

from longterm_agent.auto_longterm_agent import Ops
from news_agent.database.db_manager import DBManager

MODULE = pathlib.Path(inspect.getfile(Ops))


def _self_db_attributes(source: str) -> set:
    """Names read off `self.db` in `source`."""
    return {
        node.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "db"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "self"
    }


def _dbmanager_attributes() -> set:
    """Class members plus whatever `__init__` assigns to self — an instance attribute like
    `db_config` is a legitimate thing to reach for and is not on the class."""
    init = ast.parse(textwrap.dedent(inspect.getsource(DBManager.__init__)))
    assigned = {
        t.attr
        for node in ast.walk(init)
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"
    }
    return set(dir(DBManager)) | assigned


def test_scanner_sees_a_self_db_call():
    """Keeps the guard below honest: the module has no `self.db.<attr>` call today, so a
    scanner that quietly stopped matching would leave it passing on anything."""
    assert _self_db_attributes("class C:\n    def m(self):\n        self.db.get_cursor()\n") \
        == {"get_cursor"}


def test_every_db_attribute_the_scheduler_uses_exists():
    known = _dbmanager_attributes()
    missing = sorted(a for a in _self_db_attributes(MODULE.read_text()) if a not in known)
    assert not missing, f"scheduler calls DBManager.{missing} — no such attribute"


def test_cursor_helper_commits():
    """Without the commit the broker writes through this cursor never land."""
    assert "conn.commit()" in inspect.getsource(Ops._cursor)
