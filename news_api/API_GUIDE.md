# News API — consumer guide

Raw market-news feed over HTTP: Bloomberg (technology / economics / industries /
markets), CNBC (top / technology / finance / earnings / economy / energy) and
MarketWatch (top), collected every 5 minutes, ~250 items/day, English.

This is the **raw firehose** — no filtering, no dedup across outlets, no
opinion removal. It is the Stage-0 input for your pipeline
(`NEWS_FILTERING_PIPELINE.md`): everything your keyword funnel and classifier
expect to see, served incrementally over HTTP instead of a `raw_scrape.db`.

- **Base URL:** `http://<HOST>:8710` — the host and the API key are sent to you
  separately.
- **Auth:** every data request needs the header `X-API-Key: <your key>`.
  Missing/wrong key → `401`.

---

## Endpoints

### `GET /v1/health`
No auth. `{"status":"ok"}` when the service and its DB are up; `503` otherwise.

### `GET /v1/news` — the feed

Returns items inside a time window, ordered by time ascending.

| Query param | Default | Description |
|---|---|---|
| `since_ts` | — | Window start, **inclusive**. ISO8601 (`2026-07-22T00:00:00Z`); a timestamp without timezone is treated as UTC. |
| `until_ts` | — | Window end, **exclusive**. |
| `ts_field` | `ts` | Which timestamp the window applies to: `ts` = publication time, `fetched` = when the item arrived in our DB. **Use `fetched` for incremental polling** (see below). |
| `sources` | all | Comma-separated whitelist of source labels, e.g. `cnbc-top,bloomberg-markets`. |
| `limit` | `200` | Page size, 1–1000. |
| `offset` | `0` | Page start inside the window; continue with `next_offset` from the response. |
| `body_chars` | `1000` | Server-side truncation of `body`, 100–20000. |

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
      "link": "https://www.cnbc.com/2026/07/22/...",
      "fetched_at": "2026-07-22T13:10:03Z"
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `ts` | Original publication time (RSS), ISO8601 UTC. Falls back to arrival time when the feed carried no date. Maps to your output `ts` (§6). |
| `headline` | Article title. All sources are English. |
| `body` | Best available text: scraped full text when we have it, otherwise the RSS summary. Expect mostly short summaries — enough for your Stage 1 (500 chars) and Stage 2 (300 chars). Truncated to `body_chars`. |
| `source` | Feed label (see `/v1/sources`). Section metadata for your Stage-1 drop and §5 source ranking. |
| `link` | Article URL, **unique per item** — use it as your `raw_id` / dedup key (§8): put a unique index on it and re-runs or overlapping windows never duplicate. |
| `fetched_at` | When the item landed in our DB, UTC. The `ts_field=fetched` window filters on this. |

### `GET /v1/sources` — source inventory

Auth required. Distinct labels with item counts and the latest timestamp:

```json
{"sources": [{"source": "bloomberg-markets", "items": 13816, "latest_ts": "2026-07-22T18:49:46Z"}, ...]}
```

Current labels: `bloomberg-technology`, `bloomberg-economics`,
`bloomberg-industries`, `bloomberg-markets`, `cnbc-top`, `cnbc-technology`,
`cnbc-finance`, `cnbc-earnings`, `cnbc-economy`, `cnbc-energy`,
`marketwatch-top` (+ a few small legacy labels you can ignore). There is no
opinion/lifestyle feed in the mix — per-article opinion detection is on your
side (Stage 2).

---

## Incremental polling — the recommended pattern

Poll by **arrival time**, not publication time: an article can be published
days before we manage to fetch it, so a publication-time window advanced on
every poll silently loses late arrivals; an arrival-time window cannot.

Keep a small overlap and dedup on `link`:

```python
import requests
from datetime import datetime, timedelta, timezone

BASE, KEY = "http://<HOST>:8710", "<your key>"

def poll(last_poll_utc: datetime) -> datetime:
    """Fetch everything that arrived since the last poll. Returns the new watermark."""
    now = datetime.now(timezone.utc)
    window_start = last_poll_utc - timedelta(minutes=15)   # overlap heals restarts/clock skew
    offset = 0
    while True:
        r = requests.get(f"{BASE}/v1/news", headers={"X-API-Key": KEY}, params={
            "ts_field": "fetched",
            "since_ts": window_start.isoformat(),
            "until_ts": now.isoformat(),
            "limit": 500,
            "offset": offset,
        }, timeout=30)
        r.raise_for_status()
        data = r.json()
        upsert(data["items"])          # ON CONFLICT (link) DO NOTHING → idempotent
        if not data["has_more"]:
            return now                 # persist as the next last_poll_utc
        offset = data["next_offset"]
```

Hourly cron is fine (we ingest every 5 minutes). Backfill: the archive starts
2026-02-24 — page through it once with `ts_field=fetched` from that date.

Your `run.py --since <ts>` maps directly: `since_ts=<ts>` (+`ts_field=fetched`
for the incremental high-water-mark semantics of §8).

## Errors

| Code | Meaning |
|---|---|
| `401` | Missing or wrong `X-API-Key`. |
| `422` | Invalid parameter (bad timestamp, `limit`/`body_chars` out of range, unknown `ts_field`) — the JSON body says which. |
| `503` | Database temporarily unreachable — retry with backoff. |

Please keep `limit ≤ 500` on regular polls and don't parallelize page fetches —
it's a small read-only service, one polling loop is plenty for its volume.
