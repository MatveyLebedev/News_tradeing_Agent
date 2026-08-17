# Neo4j Schema — event / story graph

Data-model reference for the graph: node labels, relationship types, every property, the
constraints and indexes that exist on the live server, and how the graph joins back to
PostgreSQL.

Companion documents:
* [DATABASE_POSTGRES.md](DATABASE_POSTGRES.md) — the relational half.
* [../graph_db/GRAPH_DB.md](../graph_db/GRAPH_DB.md) — how the graph is *built*: LLM
  extraction, the dedup pipeline, story assignment thresholds, agent tools, cost model.
  This document covers **shape**; that one covers **process**.

**Live snapshot taken 2026-08-02** — counts are illustrative of scale, not contractual.

| | |
|---|---|
| Server | Neo4j 5 (Docker service `neo4j`) |
| Protocol | Bolt — `7687` inside Docker, **`7688`** from outside the server |
| Browser | `http://localhost:7474` |
| Nodes | Event 14,331 · Article 28,644 · Story 13,636 · Company 2,740 |
| Relationships | SOURCED_FROM 44,752 · PART_OF 27,768 · AFFECTS 26,583 · CAUSES 10,193 · RELATED_TO 3,272 |

Connection env vars: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`.

---

## The model in one line

```
Article  ◄──SOURCED_FROM──  Event  ──PART_OF──►  Story
                              │
                          AFFECTS
                              ▼
                           Company
```

A three-level hierarchy (Tencent Story Forest / Microsoft NEON lineage):

* **Article** — an immutable reference to a row in PostgreSQL `news`. Carries no content.
* **Event** — one concrete real-world fact. Many duplicate articles collapse into one Event.
* **Story** — a multi-day narrative grouping Events by topic/company. Its `article_count` is
  the market-attention signal.
* **Company** — the ticker an Event affects.

The direction of every edge is fixed; there are no reverse variants. Verified against the
live graph, only these five shapes exist:

| Shape | Count |
|---|---|
| `(Event)-[:SOURCED_FROM]->(Article)` | 44,752 |
| `(Event)-[:PART_OF]->(Story)` | 27,768 |
| `(Event)-[:AFFECTS]->(Company)` | 26,583 |
| `(Event)-[:CAUSES]->(Event)` | 10,193 |
| `(Event)-[:RELATED_TO]->(Event)` | 3,272 |

---

## Nodes

### `Event`

One real-world fact, extracted by LLM from an article's title+summary and deduplicated
against existing events.

| Property | Type | Notes |
|---|---|---|
| `id` | string (UUID) | **UNIQUE constraint.** Also the PK of `graph_event_embeddings.event_id` in Postgres |
| `canonical_id` | string | **UNIQUE constraint.** `{canonical_entity}_{event_type}_{canonical_period}`, assembled in code (not by the LLM) from two structured LLM fields. E.g. `NVDA_product_launch_2026-W11`, `iran_geopolitical_conflict_2026-03`. Exact-match on this resolves ~60–70% of duplicates with zero LLM calls |
| `name` | string | short label, 5–10 words |
| `type` | string | one of the 13 event types below (a `Literal` in the Pydantic schema, so no free-form values) |
| `description` | string | 1–2 sentence factual summary |
| `date` | datetime | event date — **the PIT clock for every agent read** |
| `confidence` | float | 0.0–1.0; updated upward on merge |
| `keywords` | list\<string\> | |
| `created_at` | datetime | |
| `updated_at` | datetime | |

> `canonical_period` can be `unknown` when the article states no date — such events fall
> back to embedding-based resolution rather than exact match.
>
> `date` is frequently date-only (midnight) and the graph carries dates from **1978 to
> 2027**. Anything that needs a real intraday instant must use
> `SOURCED_FROM.published_at`, not `e.date`.

**Event types**

| Type | Example |
|---|---|
| `geopolitical_conflict` | Iran war, Strait of Hormuz blockade |
| `macro_economic` | inflation, rates, GDP, recession |
| `commodity_price` | oil / gas / metals moves |
| `corporate_announcement` | guidance, revenue forecasts |
| `product_launch` | Nvidia Blackwell/Rubin |
| `earnings` | quarterly reports |
| `merger_acquisition` | M&A deals |
| `trade_policy` | tariffs, trade agreements |
| `regulatory` | FCC / SEC / FDA decisions |
| `market_move` | significant index moves |
| `leadership_change` | CEO / exec changes |
| `legal` | lawsuits, court cases |
| `other` | — |

### `Story`

A running narrative. Events accumulate into it over days or months.

| Property | Type | Notes |
|---|---|---|
| `key` | string | **UNIQUE constraint.** Joins to `graph_story_centroids.story_key` in Postgres. Format `{ticker}_{uuid8}_{slug}` for company stories, `{domain}_{uuid8}_{slug}` for domain stories |
| `id` | string (UUID) | |
| `label` | string | LLM-generated canonical label, 3–6 words, describing the *broad* narrative — e.g. `Iran-US Military Tensions`, not one specific event |
| `domain` | string | `geopolitics` · `macro_markets` · `company_events` · `regulatory` |
| `start_date` | datetime | story birth; drives the event-type-specific temporal window during candidate search |
| `tickers` | list\<string\> | may be empty for macro/geopolitics stories |
| `keywords` | list\<string\> | |
| `article_count` | integer | **market attention signal** — total articles that fed this story |
| `status` | string | `active` · `closed` · `merged` |
| `updated_at` | datetime | last event added; drives auto-close |

**Domain → event types**

| Domain | Event types |
|---|---|
| `geopolitics` | `geopolitical_conflict`, `trade_policy` |
| `macro_markets` | `macro_economic`, `commodity_price`, `market_move` |
| `company_events` | `corporate_announcement`, `product_launch`, `earnings`, `merger_acquisition`, `leadership_change` |
| `regulatory` | `regulatory`, `legal` |

**Status lifecycle.** `active` → `closed` after `STORY_INACTIVITY_DAYS` (default 14) with no
new events, via `close_stale_stories()`. `active` → `merged` when the weekly
`merge_duplicate_stories()` pass folds a near-duplicate into a survivor (survivor = higher
`article_count`; `PART_OF` edges are re-pointed). Both transitions are mirrored into
`graph_story_centroids.status` in Postgres, and candidate search filters
`WHERE status = 'active'` — so closed and merged stories are still **readable** but never
**candidates**.

> `article_count` is the field to reason about priced-in-ness. `article_count = 14` on a
> product-launch story means the market has been watching for a while; `article_count = 2`
> means the narrative is fresh.

### `Company`

| Property | Type | Notes |
|---|---|---|
| `ticker` | string | **UNIQUE constraint.** Uppercase |
| `name` | string | optional — not populated on most live nodes |
| `sector` | string | optional — not populated on most live nodes |

Live: 2,740 nodes. The set is whatever the LLM has ever named, so it is much wider than the
~121 tickers with price tables — and includes delisted symbols (e.g. `TWTR`). Do not treat
a `Company` node as proof that price data exists.

### `Article`

An immutable pointer into PostgreSQL. Deliberately contains no article text — the body
lives in `news`.

| Property | Type | Notes |
|---|---|---|
| `news_id` | integer | **UNIQUE constraint.** `= news.id` in PostgreSQL |
| `name` | string | present on some older nodes |
| `title` | string | present on a small minority of older nodes |

> `name` / `title` are legacy residue from earlier ingest versions (present on ~75% and ~13%
> of a 500-node sample). Do not read them — join to `news` for title and body.

---

## Relationships

### `(Event)-[:AFFECTS]->(Company)`

The economically meaningful edge: this event affects this company, in this direction, and
here is what the stock actually did.

| Property | Type | Notes |
|---|---|---|
| `sentiment` | string | `positive` · `negative` · `neutral` |
| `confidence` | float | 0.0–1.0 |
| `evidence_text` | string | **verbatim** quote from the source article grounding the claim |
| `move_15m` | float | % return 15 min after the event |
| `move_40m` | float | % return 40 min after |
| `move_4h` | float | % return 4 h (240 min) after |
| `move_1d` | float | % return 1 day (1440 min) after |
| `move_checked_at` | datetime | last fill attempt |
| `move_unavailable` | boolean | tombstone: this edge can never be filled |

**How the `move_*` fields are computed** (`update_price_movements()` in
[`graph_db/event_updater.py`](../graph_db/event_updater.py#L345), run as a background job
*after* the price fetcher, never during ingestion):

* Baseline = first bar in `historical_data_<ticker>` at/after the publish instant. The
  instant is `min(SOURCED_FROM.published_at)` across the event's articles, falling back to
  `e.date` — because `e.date` is usually midnight and has no market data, which is why every
  `AFFECTS` move used to come back null.
* For each window, the end bar is the first bar at/after `pub + window`. If the end bar *is*
  the baseline bar (after-hours or weekend news, where both resolve to the next session
  open), the window is **skipped** rather than recorded as a misleading `0.0`.
* Selection is keyed on `move_1d IS NULL` — the last window to mature — with a 4-hour
  retry cooldown (`move_checked_at`). Keying on `move_15m` used to drop events from the
  queue as soon as the 15-minute window filled, so `move_1d` never filled for live events.
* Edges older than `EVENT_MOVES_TOMBSTONE_DAYS` (default 7) that still yield nothing get
  `move_unavailable = true`. Intraday bars only accumulate forward, so an event predating
  the tables — or on a ticker with no table, or with a junk `e.date` — can never fill.
  Tombstoning stops the retry loop from hammering it forever.

Live fill rate on a 500-edge sample: `move_1d` 83, `move_4h` 41, `move_15m`/`move_40m` 21,
`move_unavailable` 417. Sparse by design — most of the graph predates the minute tables.

> **Look-ahead hazard.** `move_*` is realized future price. Agent read paths strip these
> keys (`_MOVE_KEYS` in [`longterm_agent/tools/analysis_tools.py:50`](../longterm_agent/tools/analysis_tools.py#L50))
> so an agent asking about a fresh event cannot be handed the answer. Only the explicit,
> as-of-windowed reaction tool may read them, and only for events whose window has fully
> matured before the decision moment.

### `(Event)-[:SOURCED_FROM]->(Article)`

Provenance. One event can be sourced from many articles — that is exactly what deduplication
produces (44,752 edges over 14,331 events ≈ 3.1 articles per event).

| Property | Type | Notes |
|---|---|---|
| `news_id` | integer | duplicate of `Article.news_id`, for cheap reads |
| `published_at` | datetime | **the real intraday publish instant** — the only trustworthy timestamp for intraday work |
| `move_15m`, `move_40m`, `move_4h`, `move_1d` | float | same windows, computed per affected ticker |
| `move_checked_at` | datetime | |
| `move_unavailable` | boolean | |

> `published_at` is present on only ~4% of live edges (added later than most ingest).
> Fall back to `news.published` in PostgreSQL via `news_id` when it is missing.

### `(Event)-[:PART_OF]->(Story)`

No properties. Membership only. Re-pointed by `merge_duplicate_stories()` when two stories
are folded together.

### `(Event)-[:CAUSES]->(Event)` and `(Event)-[:RELATED_TO]->(Event)`

Intra-article relations extracted in the same LLM call as the events themselves, written
after all of the article's events are resolved.

| Property | Type | Notes |
|---|---|---|
| `confidence` | float | 0.0–1.0 |
| `evidence_text` | string | **verbatim** quote grounding the relation |

`CAUSES` is directional; `RELATED_TO` is written directionally but should be traversed
undirected (`-[:RELATED_TO]-`).

> `evidence_text` on every relationship is the anti-hallucination mechanism: a relation that
> cannot be grounded in a literal sentence from the article does not get written.

---

## Constraints and indexes (live)

Created by `Neo4jManager._init_schema()` on startup
([`graph_db/neo4j_manager.py`](../graph_db/neo4j_manager.py)).

**Uniqueness constraints** — each also backs a RANGE index:

| Label | Property |
|---|---|
| `Event` | `id` |
| `Event` | `canonical_id` |
| `Story` | `key` |
| `Company` | `ticker` |
| `Article` | `news_id` |

**Additional indexes:**

| Label | Properties |
|---|---|
| `Event` | `(type, date)` |
| `Event` | `(date)` |
| `Story` | `(domain, start_date)` |
| `Story` | `(status, updated_at)` |

Plus the two built-in token-lookup indexes (node + relationship). All `ONLINE`.

```cypher
CREATE CONSTRAINT IF NOT EXISTS FOR (e:Event)   REQUIRE e.id           IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (e:Event)   REQUIRE e.canonical_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (c:Company) REQUIRE c.ticker       IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (a:Article) REQUIRE a.news_id      IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (s:Story)   REQUIRE s.key          IS UNIQUE;
CREATE INDEX IF NOT EXISTS FOR (e:Event) ON (e.type, e.date);
CREATE INDEX IF NOT EXISTS FOR (e:Event) ON (e.date);
CREATE INDEX IF NOT EXISTS FOR (s:Story) ON (s.domain, s.start_date);
CREATE INDEX IF NOT EXISTS FOR (s:Story) ON (s.status, s.updated_at);
```

There is **no vector index in Neo4j.** All similarity search happens in pgvector — see
below.

---

## Join points with PostgreSQL

The two databases are one logical model. Neo4j holds structure; Postgres holds text, vectors
and prices. There are no foreign keys across the boundary — consistency is the writer's job.

| Neo4j | PostgreSQL | Used for |
|---|---|---|
| `Article.news_id` | `news.id` | article title, summary, full text, `published`, embedding |
| `Event.id` | `graph_event_embeddings.event_id` | ANN dedup search (±21-day window) |
| `Story.key` | `graph_story_centroids.story_key` | ANN story-assignment search; `status` mirrored both ways |
| `Company.ticker` | `historical_data_<ticker>.timestamp` | the `move_*` computation |
| `Company.ticker` | `stock_prices_daily.ticker` | daily returns, benchmarks |

Why the split: Neo4j 5 has vector indexes, but the resolution pipeline already needed
PostgreSQL for the article corpus, and pgvector's HNSW at this data size is ~2 ms for a full
scan over thousands of events. Keeping vectors in one place avoids a second copy that can
drift.

Orphan risk runs one way: deleting rows from `graph_event_embeddings` (e.g. by
`EVENT_EMBEDDING_RETENTION_DAYS`) leaves the Event node intact and simply makes it
unfindable by similarity — which is the intended behaviour, not corruption.

---

## Reading the graph safely

**Every agent read must be as-of gated.** All five tools in
`news_agent/tools/graph_tools.py` filter `e.date <= $pub`, where `$pub` is the publish
instant of the article being analyzed. Without that filter a backtest silently reads events
that did not exist yet. The same rule applies to any ad-hoc query used to produce a number
that will be compared against a live run.

Agents never write Cypher. The tools use predefined parameterized queries — there is no
`GraphCypherQAChain` in the read path.

### Common queries

Events affecting a ticker, with their causal neighbours:

```cypher
MATCH (e:Event)-[r:AFFECTS]->(c:Company {ticker: $ticker})
WHERE e.date <= datetime($pub)
  AND e.date >= datetime($pub) - duration({days: $days})
OPTIONAL MATCH (e)-[cause:CAUSES|RELATED_TO]-(other:Event)
WHERE other.date <= datetime($pub)
RETURN e.id, e.name, e.type, e.description, e.date,
       r.sentiment, r.confidence,
       collect({rel: type(cause), event: other.name}) AS related
ORDER BY e.date DESC;
```

The narrative around a ticker or domain, ranked by attention:

```cypher
MATCH (s:Story)
WHERE ($q IN s.tickers OR s.domain = $q)
  AND s.start_date >= datetime($pub) - duration({days: 30})
WITH s ORDER BY s.article_count DESC LIMIT 3
MATCH (e:Event)-[:PART_OF]->(s)
WHERE e.date <= datetime($pub)
RETURN s.label, s.domain, s.article_count, s.start_date,
       collect({name: e.name, type: e.type, date: e.date}) AS events
ORDER BY s.article_count DESC;
```

Causal chain from a known event, carrying the grounding evidence:

```cypher
MATCH path = (start:Event {id: $id})-[:CAUSES|RELATED_TO*1..2]-(related:Event)
WHERE related.date <= datetime($pub)
RETURN related.id, related.name, related.type, related.date,
       [r IN relationships(path) | {type: type(r), evidence: r.evidence_text}] AS chain
ORDER BY related.date DESC;
```

Health check — orphans and unresolved fills:

```cypher
MATCH (e:Event) WHERE NOT (e)-[:PART_OF]->(:Story) RETURN count(e) AS story_orphans;
MATCH ()-[r:AFFECTS]->() WHERE r.move_1d IS NULL AND r.move_unavailable IS NULL
RETURN count(r) AS moves_pending;
```

---

## Maintenance jobs

| Job | Cadence | Env | Effect |
|---|---|---|---|
| `close_stale_stories()` | daily (`GRAPH_CLEANUP_TIME`) | `STORY_INACTIVITY_DAYS` = 14 | `active` → `closed` in Neo4j **and** `graph_story_centroids` |
| `merge_duplicate_stories()` | weekly (`GRAPH_MERGE_WEEKDAY`/`GRAPH_MERGE_TIME`) | threshold 0.88 | pairwise cosine over active centroids → LLM confirms → re-point `PART_OF`, mark `merged` |
| `update_price_movements()` | 5-min loop | `EVENT_MOVES_BATCH` = 300, `EVENT_MOVES_TOMBSTONE_DAYS` = 7 | fills `move_*` on `AFFECTS` / `SOURCED_FROM` in `LIMIT batch` slices |

`update_price_movements` is batched because the original version pulled every null edge
(23k+) with an aggregation in a single query, which died on the server's Neo4j before
writing a single row — it silently filled nothing in production until 2026-07-15.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `NEO4J_URI` | yes | `bolt://localhost:7687` | `bolt://neo4j:7687` inside Docker; port `7688` externally |
| `NEO4J_USER` | yes | `neo4j` | |
| `NEO4J_PASSWORD` | yes | — | also feeds `NEO4J_AUTH` in docker-compose |
| `GRAPH_LLM_MODEL` | no | `google/gemini-2.0-flash-lite-001` | extraction, linker, story assignment |
| `STORY_THRESHOLD_HIGH` | no | `0.90` | auto-merge into story above this cosine |
| `STORY_THRESHOLD_LOW` | no | `0.65` | always create a new story below this |
| `STORY_INACTIVITY_DAYS` | no | `14` | auto-close threshold |
| `EVENT_MOVES_BATCH` | no | `300` | edges per `update_price_movements` slice |
| `EVENT_MOVES_TOMBSTONE_DAYS` | no | `7` | age after which an unfillable edge is tombstoned |
| `EVENT_EMBEDDING_RETENTION_DAYS` | no | — | prunes `graph_event_embeddings` |

## Regenerating this document's live figures

```cypher
CALL db.labels() YIELD label
CALL { WITH label MATCH (n) WHERE label IN labels(n) RETURN count(n) AS c }
RETURN label, c ORDER BY c DESC;
```

```cypher
MATCH (a)-[x]->(b)
RETURN DISTINCT labels(a) AS from, type(x) AS rel, labels(b) AS to, count(*) AS c
ORDER BY c DESC;
```
