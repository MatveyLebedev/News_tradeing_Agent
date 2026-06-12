# Graph DB Events — Neo4j + LangChain Tools

Neo4j graph layer added on top of the existing PostgreSQL news database.  
Every newly fetched article is automatically processed: an LLM extracts structured events, deduplicates them against the graph, and links them to companies and running narratives (Stories).  
The TradingAgent gets 5 new tools to query this graph when analyzing a news article.

---

## Architecture

```
news_fetcher.py                    PostgreSQL
(bloomberg_news_fetcher)           (news table + pgvector)
        │                                  ▲
        │ 1. save article                  │
        │──────────────────────────────────┘
        │ 2. trigger extraction
        ▼
event_extractor.py  ──(LLM structured output)──►  event_updater.py
                                                         │
                                    3. structured         │  4. MERGE Event
                                    events + relations    │     MERGE Story
                                                         │     link Article
                                                         ▼
                                                       Neo4j
                                              (Article → Event → Story)
                                                         ▲
                                                         │ Cypher queries
                                                         │
                                                  graph_tools.py  ◄── pgvector (PostgreSQL)
                                                  (5 LangChain tools)
                                                         ▲
                                                         │ tool calls
                                                  TradingAgent (LangGraph)
```

---

## Neo4j Graph Schema

3-level hierarchy based on Tencent Story Forest / Microsoft NEON approach:

```
Article  ──SOURCED_FROM──►  Event  ──PART_OF──►  Story
(article ref)              (fact)               (narrative)
```

- **Article** — immutable reference node pointing to an article in PostgreSQL
- **Event** — one concrete real-world fact (may aggregate multiple duplicate articles)
- **Story** — multi-day narrative grouping Events by topic/company; stores `article_count` — market attention signal

### Nodes

| Label | Properties |
|---|---|
| `Event` | `id` (UUID), `canonical_id`, `name`, `type`, `description`, `date`, `confidence`, `keywords[]`, `created_at`, `updated_at` |
| `Story` | `id`, `key` (unique), `label`, `domain`, `start_date`, `tickers[]`, `keywords[]`, `article_count`, `status` (`active`/`closed`), `updated_at` |
| `Company` | `ticker` (unique), `name`, `sector` |
| `Article` | `news_id` (INT, FK → PostgreSQL `news.id`) |

### Relationships

| Relationship | Direction | Properties |
|---|---|---|
| `AFFECTS` | `(Event)→(Company)` | `sentiment`, `confidence`, `evidence_text` |
| `CAUSES` | `(Event)→(Event)` | `confidence`, `evidence_text` |
| `RELATED_TO` | `(Event)↔(Event)` | `strength`, `evidence_text` |
| `PART_OF` | `(Event)→(Story)` | — |
| `RELATED_TO` | `(Story)↔(Story)` | — |
| `SOURCED_FROM` | `(Event)→(Article)` | `news_id` |

> `evidence_text` is a verbatim quote from the source article that grounds every relationship and prevents LLM hallucinations.

### Story Domains

| Domain | Event types |
|---|---|
| `geopolitics` | `geopolitical_conflict`, `trade_policy` |
| `macro_markets` | `macro_economic`, `commodity_price`, `market_move` |
| `company_events` | `corporate_announcement`, `product_launch`, `earnings`, `merger_acquisition`, `leadership_change` |
| `regulatory` | `regulatory`, `legal` |

### Event Types

| Type | Example |
|---|---|
| `geopolitical_conflict` | Iran war, Strait of Hormuz blockade |
| `macro_economic` | Inflation, interest rates, GDP, recession |
| `commodity_price` | Oil/gas/metals price moves |
| `corporate_announcement` | Company forecasts, revenue guidance (Nvidia $1T) |
| `product_launch` | Product releases (Nvidia Blackwell/Rubin) |
| `earnings` | Quarterly earnings reports |
| `merger_acquisition` | M&A deals |
| `trade_policy` | Trade agreements, tariffs (EU-Australia) |
| `regulatory` | Regulator decisions (FCC, SEC, FDA) |
| `market_move` | Significant market/index moves |
| `leadership_change` | CEO/executive changes |
| `legal` | Lawsuits, court cases |
| `other` | — |

---

## News Processing Pipeline

### Overview

```
title + summary + published_at
        │
        ▼
LLM Step 1: Extract
  name, type, tickers, source_quote, intra_relations
        │
        ├── Batch embed all events (1 API call per article)
        │
        ▼
Per event — Resolution pipeline:
        │
        ├─ Stage 0: canonical_id exact match? ──YES──► MERGE (no LLM, ~60-70% of duplicates)
        │
        └─ Stage 1: pgvector cosine search (date ±21 days, LIMIT 10)
                │
                ├─ cosine > 0.88  ──────────────────► auto-merge (no LLM)
                ├─ cosine 0.65–0.88  ───► LLM Step 2: merge or create?
                └─ cosine < 0.65  ──────────────────► CREATE new Event
        │
        ├── AFFECTS Company (per ticker)
        ├── SOURCED_FROM Article
        └── CAUSES / RELATED_TO (intra-article relations)
        │
        ▼
Batch Story Assignment (all events of the article):
        │
        ├─ pgvector ANN search across ALL active story centroids (no domain/ticker filter)
        │   filtered by start_date temporal window (event-type specific, 7–180 days)
        │
        ├─ score >= 0.90  ──────────────────────► auto-merge into Story (no LLM)
        ├─ score 0.65–0.90  ──► LLM Step 3: batch assign (1 call per article, top-5 candidates)
        └─ score < 0.65  ──────────────────────► LLM Step 4: canonical label → CREATE new Story

Weekly cron: merge_duplicate_stories()
        ├─ pairwise cosine on all active centroids
        ├─ pairs >= 0.88 → LLM confirms same narrative
        └─ confirmed → re-point PART_OF edges, status='merged'
```

---

### Step 1 — LLM Extraction (`event_extractor.py`)

Model: `GRAPH_LLM_MODEL` env var (default `google/gemini-2.0-flash-lite-001`, `temperature=0`).  
Input: `title + summary` of the article.  
Output: structured `ExtractionResult` via `llm.with_structured_output()`.

Using `event_type` as `Literal[...]` in the Pydantic schema eliminates hallucinations in type values.  
`source_quote` is a verbatim sentence from the article that grounds each event.

**Pydantic schema:**

```python
class ExtractedEvent(BaseModel):
    name: str               # Short label, 5-10 words
    event_type: EventType   # Literal enum — no free-form values
    description: str        # 1-2 sentence factual summary
    date: Optional[str]     # ISO 8601 if explicitly stated in article
    tickers: List[str]      # Affected US stock tickers
    sentiment: Literal["positive", "negative", "neutral"]
    source_quote: Optional[str]  # Verbatim key sentence from article
    confidence: float       # 0.0–1.0
    canonical_entity: str   # "NVDA" (ticker) or "iran" / "federal-reserve" (macro)
    canonical_period: str   # "2026-W11" (company) or "2026-03" (macro)

class IntraRelation(BaseModel):
    from_idx: int           # Index into events[]
    to_idx: int
    rel_type: Literal["CAUSES", "RELATED_TO"]
    evidence_text: str      # Verbatim quote grounding the relationship
    confidence: float

class ExtractionResult(BaseModel):
    events: List[ExtractedEvent]
    intra_relations: List[IntraRelation]
```

---

### Stage 0 — canonical_id Exact Match (no LLM)

`canonical_id` is assembled in code — not by the LLM — from two structured fields the LLM returns:

```python
canonical_id = f"{ev.canonical_entity}_{ev.event_type}_{ev.canonical_period}"

# Examples:
# "NVDA_product_launch_2026-W11"        → all GTC articles in the same week
# "iran_geopolitical_conflict_2026-03"  → all Iran war articles in March
```

**Lookup in Neo4j:**

```cypher
MATCH (e:Event {canonical_id: $cid}) RETURN e.id AS id
```

- **Match found** → MERGE: add `SOURCED_FROM` to the new Article, update `confidence` if higher
- **No match** → proceed to Stage 1

Handles ~60-70% of duplicate articles with zero LLM calls.

---

### Stage 1 — pgvector Cosine Search

If `canonical_id` did not match, the event embedding is searched against existing events in PostgreSQL:

```sql
SELECT event_id, 1 - (embedding <=> $query_vec) AS score
FROM graph_event_embeddings
WHERE event_date BETWEEN $pub - INTERVAL '21 days' AND $pub
ORDER BY embedding <=> $query_vec
LIMIT 10
```

`$pub` = `published_dt` from PostgreSQL (not `fetch_time`).  
Single date filter only — no structural blocking by ticker or keyword. At 5,000 events a full pgvector scan takes ~2ms.

**Cosine thresholds** (from entity resolution literature):

| Score | Action |
|---|---|
| `> 0.88` | Auto-merge — high confidence, no LLM |
| `0.65–0.88` | LLM Linker — pass top-5 candidates to LLM to decide |
| `< 0.65` | CREATE new Event |

**Why no structural blocking:**
- Avoids the "542 candidates" problem (`geopolitical_conflict` over 7 days)
- No separate ticker / macro branches — one unified query
- GTC day-1 event is found via cosine ~0.91 even with 4 other NVDA events in between
- Macro events ("Iran war" ↔ "Iran oil supply disruption") are found semantically without keywords

---

### LLM Linker (Stage 1, medium cosine 0.65–0.88)

When cosine search returns ambiguous candidates, the LLM receives the new event plus up to 5 existing candidates and decides whether to merge or create:

**Prompt:**
```
New event: {name}
{description}

Existing candidates:
[uuid1] Event name — description
[uuid2] Event name — description

Does the new event describe the SAME real-world fact as one of the candidates?
If yes, return its event_id in merge_into. If it is a distinct event, return null.
```

**Structured response:** `LinkDecision { merge_into: str | null }`

Applies to ~35% of new articles (those that pass stage 0 and land in the medium cosine band).

---

### Story Assignment (pure pgvector + batch LLM)

After all events of an article are resolved, they are assigned to Stories in a **two-pass batch process**.

**Step 1 — Candidate search via pgvector ANN (no domain/ticker filter):**

For each event, PostgreSQL `graph_story_centroids` is queried with a direct ANN search against the event embedding. There is **no domain or ticker pre-filter** — all active stories are candidates regardless of domain. This allows cross-domain stories to be found (e.g. an oil-price event finding an Iran-war geopolitics story).

```sql
SELECT story_key, 1 - (centroid <=> $vec) AS score
FROM graph_story_centroids
WHERE status = 'active'
  AND (start_date IS NULL OR start_date >= $pub - INTERVAL '$window days')
ORDER BY centroid <=> $vec
LIMIT 20
```

The temporal window is event-type specific:

| Event type | Window (days) |
|---|---|
| `earnings` | 7 |
| `dividend` | 3 |
| `product_launch` | 21 |
| `merger_acquisition` | 90 |
| `legal` | 180 |
| `leadership_change`, `corporate_announcement` | 14 |
| `commodity_price` | 30 |
| `macro_economic` | 45 |
| `market_move` | 7 |
| `regulatory` | 60 |
| `geopolitical_conflict`, `trade_policy` | 90 |
| `other` | 30 |

After the PG vector search, labels and `article_count` for the top-20 keys are fetched from Neo4j.

**Step 2 — Three-tier decision:**

| Score | Action |
|---|---|
| `>= 0.90` | Auto-merge into best Story (no LLM) |
| `0.65–0.90` | Batch LLM decides — one call per article for all medium-zone events |
| `< 0.65` | Create new Story via LLM canonical label |

Thresholds are configurable via `STORY_THRESHOLD_HIGH` / `STORY_THRESHOLD_LOW` env vars.

**Step 3 — Batch LLM Story assigner:**

All events in the medium zone (0.65–0.90) are sent to the LLM in a **single call per article** along with their top-5 Story candidates and similarity scores. The LLM returns a structured `StoryAssignmentBatch` — for each event either a `story_key` to merge into or `create_new: true`.

If the LLM call fails, fallback logic applies: merge if score >= 0.70, else create new.

**Step 4 — New story creation with canonical label:**

When no existing story is found, the LLM generates a short (3–6 word) broad canonical label for the new story before it is written to Neo4j:

```
Prompt: "Generate a short (3-6 words) canonical news story label for this event.
The label should describe the broad ongoing narrative, not just this specific event.
Examples: 'Iran-US Military Tensions', 'Fed Rate Hike Cycle', 'Apple AI Strategy'"
```

The canonical label is used as `s.label`. The story `key` is derived from it:

```python
# Company events:  NVDA_a1b2c3d4_apple_ai_strategy
key = f"{ticker}_{uuid8}_{slug}"

# Domain events:   geopolitics_a1b2c3d4_iran_us_military_tensions
key = f"{domain}_{uuid8}_{slug}"
```

The UUID prefix ensures uniqueness while the slug keeps keys human-readable.

**Centroid management:** Each Story stores a centroid embedding in PostgreSQL (`graph_story_centroids`), updated incrementally on every merge: `centroid = L2_normalize((old * N + new_emb) / (N + 1))`.

`Story.article_count` increments on every merge — this is the **market attention signal** readable by the agent via `get_story_context`.

### Story Auto-Close

Stories are automatically closed after **14 days** of inactivity (no new events). The `close_stale_stories()` function is intended to run via cron (e.g. daily):

1. Finds all Story nodes where `status = 'active'` and `updated_at < now - 14 days`
2. Sets `status = 'closed'` in Neo4j
3. Sets `status = 'closed'` in `graph_story_centroids` in PostgreSQL (row is kept for audit, excluded from candidate search by `WHERE status = 'active'` filter)

Closed Stories remain in the graph for reading (via `get_story_context`) but are excluded from candidate search. Inactivity threshold is configurable via `STORY_INACTIVITY_DAYS` env var.

### Story Deduplication Consolidation (`merge_duplicate_stories`)

A weekly maintenance function that finds and merges near-duplicate Stories that slipped past real-time assignment:

```python
from graph_db import merge_duplicate_stories
merge_duplicate_stories()           # uses default similarity_threshold=0.88
```

**Algorithm:**

1. Load all active story centroids from PostgreSQL
2. L2-normalize, compute full pairwise cosine similarity matrix (numpy)
3. Collect all pairs with similarity `>= 0.88`
4. Send pairs (up to 20 at a time) to LLM for confirmation — the LLM sees story labels and decides `same_story: true/false`
5. For confirmed pairs: surviving story = higher `article_count`; re-point all `(e)-[:PART_OF]->` edges to survivor; set `status = 'merged'` in Neo4j and PostgreSQL; fold duplicate centroid into survivor

**Test result (2026-04-05, 857 active stories):** Found 21 pairs above 0.88, merged 19 confirmed duplicates — e.g. `'Iran War Impact on Oil Prices'` + `'XOM · Geopolitical instability in Iran...'` + `'BP · De-escalation...'` → all merged into `'Iran War Impacts on Global Markets'` (151 articles).

---

### Intra-Article Relations

After all events of the article are resolved, causal links extracted in Step 1 are written:

```cypher
MATCH (a:Event {id: $from_id}), (b:Event {id: $to_id})
MERGE (a)-[r:CAUSES|RELATED_TO]->(b)
ON CREATE SET r.confidence = $conf, r.evidence_text = $evidence
```

---

### `news_fetcher.py` integration

`save_to_database` was changed to `INSERT ... RETURNING id` and now returns `news_id` (int) instead of `True`.  
The extraction is called after each successful save, wrapped in `try/except` so graph failures never break the fetcher:

```python
result = save_to_database(article, verbose=False)
if isinstance(result, int):
    saved_count += 1
    try:
        from graph_db.event_updater import process_article_events
        process_article_events(article, result)   # fire-and-forget
    except Exception as e:
        logger.warning('GRAPH_FAIL', f"Event extraction failed: {e}")
```

---

## PostgreSQL Additions

Two tables created automatically on first run (reuses existing pgvector extension):

```sql
CREATE TABLE graph_event_embeddings (
    event_id   TEXT PRIMARY KEY,   -- matches Event.id in Neo4j
    embedding  vector(1536),
    event_date DATE                -- published_at, used for ±21-day filter
);
CREATE INDEX gee_date_idx ON graph_event_embeddings (event_date);

CREATE TABLE graph_story_centroids (
    story_key    TEXT PRIMARY KEY,  -- matches Story.key in Neo4j
    centroid     vector(1536),      -- L2-normalized running mean of event embeddings
    event_count  INT DEFAULT 1,
    last_updated TIMESTAMP DEFAULT NOW(),
    start_date   TIMESTAMPTZ,       -- story birth date; used for temporal window filter
    status       TEXT DEFAULT 'active'  -- 'active' | 'closed' | 'merged'
);
CREATE INDEX gsc_status_idx    ON graph_story_centroids (status);
CREATE INDEX gsc_start_date_idx ON graph_story_centroids (start_date);
```

`status` mirrors `Story.status` in Neo4j and is kept in sync by `close_stale_stories()` and `merge_duplicate_stories()`. The pgvector candidate search filters `WHERE status = 'active'` so closed/merged stories are never candidates for new events.

---

## Neo4j Constraints and Indexes

Created automatically by `Neo4jManager._init_schema()` on startup:

```cypher
CREATE CONSTRAINT IF NOT EXISTS FOR (e:Event)   REQUIRE e.id           IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (e:Event)   REQUIRE e.canonical_id IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (c:Company) REQUIRE c.ticker        IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (a:Article) REQUIRE a.news_id       IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (s:Story)   REQUIRE s.key           IS UNIQUE
CREATE INDEX IF NOT EXISTS FOR (e:Event) ON (e.type, e.date)
CREATE INDEX IF NOT EXISTS FOR (e:Event) ON (e.date)
CREATE INDEX IF NOT EXISTS FOR (s:Story) ON (s.domain, s.start_date)
CREATE INDEX IF NOT EXISTS FOR (s:Story) ON (s.status, s.updated_at)
```

---

## Agent Tools (`news_agent/tools/graph_tools.py`)

Instantiated by calling `create_graph_tools(neo4j_manager, db_manager, timestamp)`.

- `neo4j_manager` — `Neo4jManager` instance
- `db_manager` — existing DB manager with `vector_similarity_search(query_embedding, timestamp, limit)`
- `timestamp` — `published_at` datetime of the article being analyzed

**All 5 tools filter `e.date <= timestamp`** — the agent can only see events that existed before the article it is currently analyzing. No look-ahead bias.

Tools use predefined parameterized Cypher — the agent does **not** write arbitrary Cypher (no `GraphCypherQAChain`).

---

### `get_events_for_ticker`

Get all events related to a stock ticker published before the current article, within the last N days. Includes cause/effect relationship chains.

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `ticker` | `str` | required | US stock ticker, e.g. `"NVDA"` |
| `days` | `int` | `30` | Look-back window in days |

**Cypher:**
```cypher
MATCH (e:Event)-[r:AFFECTS]->(c:Company {ticker: $ticker})
WHERE e.date <= datetime($pub)
  AND e.date >= datetime($pub) - duration({days: $days})
OPTIONAL MATCH (e)-[cause:CAUSES|RELATED_TO]-(other:Event)
WHERE other.date <= datetime($pub)
RETURN e.id, e.name, e.type, e.description, e.date,
       r.sentiment, r.confidence,
       collect({rel: type(cause), event: other.name}) AS related
ORDER BY e.date DESC
```

**Returns:** JSON array of events with `related` — list of causal/related event names.

**When to use:** Before assessing the impact of a new announcement — check what has already happened to this company and whether there are causal chains connecting recent events.

---

### `get_recent_events`

Get the most recent events across the graph, optionally filtered by type.

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `limit` | `int` | `20` | Max number of events |
| `event_type` | `str \| None` | `None` | Filter by type (see list below) |

**Valid `event_type` values:**
`geopolitical_conflict` · `macro_economic` · `commodity_price` · `corporate_announcement` · `product_launch` · `earnings` · `merger_acquisition` · `trade_policy` · `regulatory` · `market_move` · `leadership_change` · `legal` · `other`

**Cypher:**
```cypher
MATCH (e:Event)
WHERE e.date <= datetime($pub)
  AND ($type IS NULL OR e.type = $type)
RETURN e.id, e.name, e.type, e.description, e.date, e.confidence
ORDER BY e.date DESC LIMIT $limit
```

**When to use:** To get a snapshot of the current macro environment — e.g. "what geopolitical events happened in the last 3 days?" Pass `event_type="geopolitical_conflict"` to narrow the scope.

---

### `find_related_events`

Traverse the cause/effect graph from a known event up to a given depth.

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `event_id` | `str` | required | UUID of the starting event |
| `depth` | `int` | `2` | Max relationship hops |

**Cypher:**
```cypher
MATCH path = (start:Event {id: $id})-[:CAUSES|RELATED_TO*1..$depth]-(related:Event)
WHERE related.date <= datetime($pub)
RETURN related.id, related.name, related.type, related.date,
       [r IN relationships(path) | {type: type(r), evidence: r.evidence_text}] AS chain
ORDER BY related.date DESC
```

**Returns:** JSON array with `chain` — list of `{type, evidence}` objects showing the path.

**When to use:** After finding an event ID from `get_events_for_ticker` or `search_events`, use this to understand the full causal context — e.g. why did oil prices spike, what did that cause downstream in energy stocks.

---

### `search_events`

Semantic search across all stored events using natural language.

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | required | Natural language query |
| `limit` | `int` | `10` | Max results |

**How it works (2-hop):**

1. Embeds the query string via `google/gemini-embedding-001` (1536 dims, via OpenRouter)
2. pgvector cosine search against `news.embedding` in PostgreSQL — filtered to `published < timestamp`
3. Takes `news_ids` from results → Cypher `MATCH (Event)-[:SOURCED_FROM]->(Article)` in Neo4j

```cypher
MATCH (e:Event)-[:SOURCED_FROM]->(a:Article)
WHERE a.news_id IN $ids AND e.date <= datetime($pub)
RETURN DISTINCT e.id, e.name, e.type, e.description, e.date, e.confidence
ORDER BY e.date DESC LIMIT $limit
```

**When to use:** Free-form search when event IDs or tickers are unknown — e.g. `"Iran oil sanctions shipping impact"`, `"Federal Reserve rate decision March 2026"`, `"semiconductor supply chain disruption"`.

---

### `get_story_context`

Get the ongoing narrative (Story) for a ticker or macro domain, including market attention signal.

**Parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `ticker_or_domain` | `str` | required | Ticker like `"NVDA"` or domain: `"geopolitics"`, `"macro_markets"`, `"regulatory"` |

**Cypher:**
```cypher
MATCH (s:Story)
WHERE ($q IN s.tickers OR s.domain = $q)
  AND s.start_date >= datetime($pub) - duration({days: 30})
WITH s ORDER BY s.article_count DESC LIMIT 3
MATCH (e:Event)-[:PART_OF]->(s)
WHERE e.date <= datetime($pub)
RETURN s.label, s.domain, s.article_count, s.start_date,
       collect({name: e.name, type: e.type, date: e.date}) AS events
ORDER BY s.article_count DESC
```

**Returns example:**
```json
{
  "label": "NVDA · Nvidia GTC keynote announces Rubin",
  "domain": "company_events",
  "article_count": 14,
  "start_date": "2026-03-11T00:00:00",
  "events": [
    {"name": "Nvidia GTC keynote announces Rubin GPU", "type": "product_launch", "date": "..."},
    {"name": "Nvidia Rubin pre-orders exceed supply",  "type": "corporate_announcement", "date": "..."}
  ]
}
```

**`article_count`** = total articles that contributed to this Story. High value means the market has been paying sustained attention to this narrative — use it as a signal strength multiplier. `article_count=14` on a product launch story means it is far more priced in than `article_count=2`.

**When to use:** Before generating a trading signal — understand whether the news is the beginning of a new narrative or the 14th article in an already well-covered story.

---

## Docker

Service added to `docker-compose.yml`:

```yaml
neo4j:
  image: neo4j:5
  container_name: neo4j
  restart: unless-stopped
  ports:
    - "7474:7474"   # Browser UI
    - "7687:7687"   # Bolt protocol
  environment:
    - NEO4J_AUTH=neo4j/${NEO4J_PASSWORD}
    - NEO4J_server_memory_heap_initial__size=256m
    - NEO4J_server_memory_heap_max__size=512m
    - NEO4J_server_memory_pagecache_size=64m   # <50 MB data even after 2 years
  volumes:
    - neo4j_data:/data
  healthcheck:
    test: ["CMD", "wget", "-q", "--spider", "http://localhost:7474"]
    interval: 15s
    timeout: 5s

volumes:
  neo4j_data:
```

Neo4j Browser is available at `http://localhost:7474` after `docker compose up`.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `NEO4J_URI` | yes | `bolt://localhost:7687` | Neo4j Bolt endpoint (`bolt://neo4j:7687` inside Docker) |
| `NEO4J_USER` | yes | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | yes | — | Neo4j password; also used in `NEO4J_AUTH` in docker-compose |
| `GRAPH_LLM_MODEL` | no | `google/gemini-2.0-flash-lite-001` | OpenRouter model for extraction, LLM linker, and Story assignment |
| `OPENROUTER_API_KEY` | yes | — | Shared with the rest of the project |
| `STORY_THRESHOLD_HIGH` | no | `0.90` | Cosine score above which events auto-merge into a Story (no LLM) |
| `STORY_THRESHOLD_LOW` | no | `0.65` | Cosine score below which a new Story is always created |
| `STORY_INACTIVITY_DAYS` | no | `14` | Days without new events before a Story is auto-closed |

---

## New and Modified Files

### New

| File | Description |
|---|---|
| `graph_db/__init__.py` | Module exports: `Neo4jManager`, `process_article_events`, `close_stale_stories`, `merge_duplicate_stories` |
| `graph_db/neo4j_manager.py` | Neo4j driver wrapper; creates constraints and indexes on startup |
| `graph_db/event_extractor.py` | Pydantic schemas + `extract_events()` via LLM structured output |
| `graph_db/event_updater.py` | Full resolution pipeline, pgvector Story assignment (no domain filter), LLM canonical labels, auto-close, weekly dedup consolidation |
| `news_agent/tools/graph_tools.py` | `create_graph_tools()` → 5 LangChain tools |

### Modified

| File | Change |
|---|---|
| `bloomberg_news_fetcher/news_fetcher.py` | `INSERT … RETURNING id` → returns `news_id`; calls `process_article_events` after save |
| `news_agent/tools/__init__.py` | Added `create_graph_tools` import and export |
| `docker-compose.yml` | Added `neo4j` service and `neo4j_data` volume |
| `requirements.txt` | Added `neo4j>=5.0`, `langchain-neo4j>=0.1.0` |
| `.env` | Added `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `GRAPH_LLM_MODEL` |

---

## Cost Estimate

Based on actual DB data: 4,307 articles, ~172 articles/day average.

Model: `google/gemini-2.0-flash-lite-001` via OpenRouter ($0.075/M input, $0.30/M output).  
Embedding: `google/gemini-embedding-001` via OpenRouter ($0.15/M input).

| Component | Tokens (in/out) | Frequency | Cost/article |
|---|---|---|---|
| Step 1 — LLM extraction | ~370 / ~250 | Every article | ~$0.000103 |
| Step 2 — LLM dedup linker | ~420 / ~20 | ~12% of events | ~$0.000011 |
| **Step 3 — LLM Story batch** | **~725 / ~125** | **~1 call per article (medium-zone events)** | **~$0.000092** |
| Step 4 — LLM canonical label | ~120 / ~15 | ~15% of events (new story creation) | ~$0.000005 |
| Event embeddings | ~150 input | Every article | ~$0.000023 |
| Story centroid ops | — | — | $0 (numpy + pgvector) |
| **Total per article** | | | **~$0.000234** |

| Volume | Daily cost | Monthly cost |
|---|---|---|
| 172 articles/day (current) | $0.039 | **$1.18** |
| 500 articles/day | $0.115 | **$3.44** |
| 1,000 articles/day | $0.229 | **$6.87** |

**Note:** `gemini-2.0-flash-lite` is being deprecated June 1, 2026. Migration to `gemini-2.5-flash` ($0.30/M input, $2.50/M output) will increase costs ~4-8x. Model is configurable via `GRAPH_LLM_MODEL` in `.env`.
