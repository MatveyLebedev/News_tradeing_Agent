"""What the labelers of one job share: today's date, a lazy Neo4j handle and price caches."""
from datetime import date
from math import isfinite
from typing import Dict, List, Optional, Tuple


class LabelContext:
    def __init__(self, today: date, db_config: dict):
        self.today = today
        self.db_config = db_config
        self.max_dates: Dict[str, Optional[date]] = {}   # grade_shifts._has_data_through cache
        self._series: Dict[str, List[Tuple[date, float]]] = {}
        self._neo = None
        self._neo_tried = False
        self._read_conn = None

    @property
    def read_cur(self):
        """An AUTOCOMMIT cursor for price reads. The grader's price helpers swallow query
        errors (exit_rules.entry_bar_mid returns None on any exception), which on the job's
        own transaction would leave it aborted: every later query fails and the labels
        written at the end are lost. Here a failed read stays one failed read."""
        if self._read_conn is None:
            import psycopg2
            self._read_conn = psycopg2.connect(**self.db_config)
            self._read_conn.autocommit = True
        return self._read_conn.cursor()

    @property
    def neo(self):
        """Neo4jManager, or None when the graph is unreachable (labels needing it wait)."""
        if not self._neo_tried:
            self._neo_tried = True
            try:
                from graph_db.neo4j_manager import Neo4jManager
                self._neo = Neo4jManager()
            except Exception as e:
                print(f"  ⚠ Neo4j unavailable — graph labels deferred ({e})")
        return self._neo

    def closes(self, ticker: str) -> List[Tuple[date, float]]:
        """The ticker's whole daily close series, oldest first, fetched once per job."""
        t = ticker.upper()
        if t not in self._series:
            cur = self.read_cur
            cur.execute("""SELECT date, close FROM stock_prices_daily
                           WHERE ticker=%s AND close IS NOT NULL ORDER BY date""", [t])
            # NaN passes IS NOT NULL (and Postgres even treats NaN = NaN as true)
            self._series[t] = [(d, float(c)) for d, c in cur.fetchall()
                               if isfinite(float(c)) and float(c) > 0]
        return self._series[t]

    def close(self):
        if self._read_conn is not None:
            self._read_conn.close()
        if self._neo is not None:
            try:
                self._neo.close()
            except Exception:
                pass
