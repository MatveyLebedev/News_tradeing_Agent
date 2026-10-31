# news-api — raw news feed over HTTP

Read-only API exposing the raw scrape firehose (Postgres `news` table) for the
external news-filtering pipeline (`NEWS_FILTERING_PIPELINE.md`). The pipeline's
Stage 0 input ("raw_scrape.db") is served by this API instead of direct DB access.

Base URL: `http://<host>:<NEWS_API_PORT>` (default port `8710`).
Auth: every data endpoint requires the `X-API-Key` header.

## Endpoints

### `GET /v1/health`
No auth. `{"status": "ok"}` when the DB is reachable, `503` otherwise.

### `GET /v1/news`
Date-window pull of raw news items, ordered by the windowed time ascending.

| Param | Default | Meaning |
|---|---|---|
| `since_ts` / `until_ts` | — | Time window `[since_ts, until_ts)`, ISO8601; naive timestamps are treated as UTC. The `--since <ts>` CLI form maps directly. |
| `ts_field` | `ts` | Which time the window filters: `ts` (publication) or `fetched` (arrival). **Use `fetched` for incremental polling** — an article can be published days before we fetch it, so a publication-time window can silently miss late arrivals; an arrival-time window cannot. |
| `limit` | `200` | Page size, max `1000`. |
| `offset` | `0` | Page start within the window; continue with `next_offset`. |
| `sources` | all | Comma-separated source labels to include (see `/v1/sources`). |
| `body_chars` | `1000` | Server-side truncation of `body` (100–20000). |

Response:
```json
{
  "count": 2,
  "has_more": true,
  "next_offset": 2,
  "items": [
    {
      "ts": "2026-07-22T13:05:12Z",
      "headline": "OPEC+ agrees to extend output cuts",
      "body": "The group said on Tuesday ...",
      "source": "cnbc-energy",
      "link": "https://www.cnbc.com/...",
      "fetched_at": "2026-07-22T13:10:03Z"
    }
  ]
}
```

Polling pattern (hourly cron is fine — the scraper writes every 5 minutes).
Window on **arrival time** with a small overlap; dedup on `link`:
```
last_poll = <stored, ISO8601 UTC>
window_start = last_poll - 15min          # overlap heals clock skew / restarts
now = utcnow()
offset = 0
loop:
    r = GET /v1/news?ts_field=fetched&since_ts={window_start}&until_ts={now}&limit=500&offset={offset}
    upsert(r.items)                        # unique index on link -> re-runs never duplicate (§8)
    if not r.has_more: break
    offset = r.next_offset
last_poll = now                            # persist it
```

### `GET /v1/sources`
Distinct source labels with item counts and latest timestamp — use it to build
the source-ranking config (§5) and the section drop list.

## Field mapping to the pipeline doc

| API field | Pipeline contract | Notes |
|---|---|---|
| `link` | `raw_id` surrogate (§6, §8) | Stable per-article key (unique in our DB). Put a unique index on it — re-runs and overlapping windows never duplicate. |
| `ts` | `ts` — ISO8601 UTC (§6) | Original RSS publication time, converted from the DB's US-Eastern storage. Falls back to fetch time when the feed gave no date. |
| `headline` | `headline` | RSS title. All current feeds are English. |
| `body` | `body` (§6: first 1000 chars) | `full_text` when scraped, else the RSS summary. Full-text scraping is currently **off** (`FETCH_FULL_TEXT=false`), so expect short RSS summaries — enough for Stage 1 (500 chars) and Stage 2 (300 chars). |
| `source` | source ranking (§5) + section drop (§3) | Labels: `bloomberg-technology`, `bloomberg-economics`, `bloomberg-industries`, `bloomberg-markets`, `cnbc-top`, `cnbc-technology`, `cnbc-finance`, `cnbc-earnings`, `cnbc-economy`, `cnbc-energy`, `marketwatch-top`. No opinion feed is fetched: Bloomberg's `opinion` slug is dead (404) and never stored a row, so there is no opinion label to drop. |
| `fetched_at` | — | When the scraper stored the row (UTC). The `ts_field=fetched` window filters on this. |

## Examples

```bash
# one day of news by publication time
curl -s -H "X-API-Key: $NEWS_API_KEY" \
  "http://<host>:8710/v1/news?since_ts=2026-07-21T00:00:00Z&until_ts=2026-07-22T00:00:00Z&limit=500"

# incremental poll by arrival time, CNBC only
curl -s -H "X-API-Key: $NEWS_API_KEY" \
  "http://<host>:8710/v1/news?ts_field=fetched&since_ts=2026-07-22T18:00:00Z&sources=cnbc-top,cnbc-energy"

# source inventory
curl -s -H "X-API-Key: $NEWS_API_KEY" "http://<host>:8710/v1/sources"
```

## Operations (our side)

- Runs as the `news-api` service in the root `docker-compose.yml`; same `.env`
  as the fetcher (`DB_*` vars) plus:
  - `NEWS_API_KEY` — shared secret for the `X-API-Key` header (required; the
    service refuses to start without it)
  - `NEWS_API_PORT` — host port to publish (default `8710`; DB-style default
    ports are ISP-blocked, so keep it non-standard)
- The API is strictly read-only: parameterized SELECTs on `news` only.
- Window filters use `idx_news_published`/`idx_news_fetch_time`; `news.id`
  serves only as an internal tiebreaker for stable offset pagination.
