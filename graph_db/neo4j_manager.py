import os
import logging
from neo4j import GraphDatabase

logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)


class Neo4jManager:
    def __init__(self, uri=None, user=None, password=None):
        uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = user or os.getenv("NEO4J_USER", "neo4j")
        password = password or os.getenv("NEO4J_PASSWORD", "")
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._init_schema()

    def _init_schema(self):
        constraints = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (e:Event) REQUIRE e.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (e:Event) REQUIRE e.canonical_id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Company) REQUIRE c.ticker IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Article) REQUIRE a.news_id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (s:Story) REQUIRE s.key IS UNIQUE",
            "CREATE INDEX IF NOT EXISTS FOR (e:Event) ON (e.type, e.date)",
            "CREATE INDEX IF NOT EXISTS FOR (e:Event) ON (e.date)",
            "CREATE INDEX IF NOT EXISTS FOR (s:Story) ON (s.domain, s.start_date)",
            "CREATE INDEX IF NOT EXISTS FOR (s:Story) ON (s.status, s.updated_at)",
        ]
        with self._driver.session() as s:
            for c in constraints:
                s.run(c)

    def query(self, cypher, params=None):
        try:
            with self._driver.session() as s:
                return [dict(r) for r in s.run(cypher, params or {})]
        except Exception:
            # Reconnect once on failure (handles stale connection pool)
            try:
                self._driver.close()
            except Exception:
                pass
            uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
            user = os.getenv("NEO4J_USER", "neo4j")
            password = os.getenv("NEO4J_PASSWORD", "")
            self._driver = GraphDatabase.driver(uri, auth=(user, password))
            with self._driver.session() as s:
                return [dict(r) for r in s.run(cypher, params or {})]

    def close(self):
        self._driver.close()
