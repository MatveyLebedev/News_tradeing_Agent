# Langfuse (self-hosted) — tracing and experiment platform

Langfuse v4 replaces LangSmith (2026-10-28). It runs as its own compose project,
`trade-langfuse`, next to the agents. Prompts carry news text and financials, so they stay on
our own machines. Every port binds to `127.0.0.1`, and the UI is reached through an SSH
tunnel, never the open internet.

| piece | what |
|---|---|
| `docker-compose.yml` | web, worker, ClickHouse, Postgres, Redis, MinIO, with pinned images, memory caps and log caps |
| `clickhouse/low-footprint.xml` | drops ClickHouse's own diagnostic logs (they grow without bound and Langfuse does not use them) |
| `gen_env.sh` | writes `.env` with fresh random secrets **on the machine that runs the stack**. It prints names only and refuses to overwrite |
| `core/tracing.py` | the agent side: one trace per run, deterministic trace id, environments `live` / `backtest` / `dev` |

## Local

```bash
LANGFUSE_PORT=3100 deploy/langfuse/gen_env.sh     # any free port; NEXTAUTH_URL follows it
cd deploy/langfuse && docker compose up -d
curl -s http://localhost:3100/api/public/health    # {"status":"OK","version":"4.70.1"}
```

The org `trade-agent`, project `longterm-agent`, its API keys and the admin user are created
headlessly on first start (`LANGFUSE_INIT_*`). Sign-up is disabled. The UI login is
`LANGFUSE_INIT_USER_EMAIL` / `LANGFUSE_INIT_USER_PASSWORD` from `deploy/langfuse/.env`.

The app side reads three variables from the repo-root `.env`:

```
LANGFUSE_PUBLIC_KEY=<LANGFUSE_INIT_PROJECT_PUBLIC_KEY>
LANGFUSE_SECRET_KEY=<LANGFUSE_INIT_PROJECT_SECRET_KEY>
LANGFUSE_BASE_URL=http://localhost:3100
```

Then any entrypoint traces: `python -m longterm_agent.run_longterm_agent --ticker KO --no-persist`.
That command traces to environment `dev` with run kind `manual`, so it never shares a trace id
with the live run of that day.

## Server

The stack lives in `/root/langfuse`. Copy the bundle **without** any `.env`, and generate
secrets there. `SMALL_HOST=1` appends the tight limits this server needs:

```bash
ssh root@<host> 'mkdir -p /root/langfuse/clickhouse'
ssh root@<host> 'cat > /root/langfuse/docker-compose.yml' < deploy/langfuse/docker-compose.yml
ssh root@<host> 'cat > /root/langfuse/gen_env.sh && chmod 755 /root/langfuse/gen_env.sh' < deploy/langfuse/gen_env.sh
ssh root@<host> 'cat > /root/langfuse/clickhouse/low-footprint.xml' < deploy/langfuse/clickhouse/low-footprint.xml
ssh root@<host> 'cd /root/langfuse && SMALL_HOST=1 ./gen_env.sh && docker compose up -d'
```

### Sharing a 3.8 GiB host with the trading databases

The server has 2 vCPU and 3.8 GiB of RAM. The stack runs at about 1.6 GiB idle, and these
guards keep it from hurting production:

| guard | effect |
|---|---|
| `oom_score_adj: 800` on every Langfuse service | when the host runs out of memory, the kernel kills Langfuse first, never Postgres or Neo4j (score ≈1200 vs ≈670) |
| `mem_limit` = `memswap_limit` | each service has a hard RAM ceiling and no swap, so Langfuse cannot push the databases into swap |
| `cpus` (web 0.75, worker 0.5, ClickHouse 1.0) | ingestion bursts cannot starve the agent or Postgres of CPU |
| `NODE_OPTIONS=--max-old-space-size` | V8 sizes its heap to the container, not the host. The web heap must be ≥1024 MB: at 512 MB it crash-looped |

What this costs:
- Langfuse can be OOM-killed under pressure (it restarts itself). Traces in flight when that
  happens are lost; the agent's run is not affected.
- Heavy UI queries (long-range dashboards) may fail ClickHouse's 1 GiB memory limit.
- Swap is shared and small (1 GiB). If the Sunday batch shows swapping or Langfuse
  restarts, the fixes are more RAM (8 GiB) or a 2 GiB swap file. Watch
  `docker inspect -f '{{.RestartCount}}' trade-langfuse-langfuse-web-1` and `free -h`.

Agents reach Langfuse over the compose network, not through a published port. Add three
lines to `/root/trade_agent/.env`, with the keys taken from `/root/langfuse/.env` on the server:

```
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_BASE_URL=http://langfuse-web:3000
```

Then attach each agent container to the network. `docker run` takes a single `--network`, so
attach the second one after the container starts. The default bridge stays attached, so DB
access is unchanged:

```bash
docker network connect trade-langfuse longterm_agent
```

`longterm_agent/DEPLOY.md` §4a includes this step in the recreate loop.

**UI:** `ssh -N -L 3000:127.0.0.1:3000 root@<host>`, then open http://localhost:3000. The local
port must equal the server's `LANGFUSE_PORT`, because NextAuth redirects to `NEXTAUTH_URL`.

## What gets traced

| entrypoint | environment | trace id seed (`run_kind:detection:ticker:date`) | session |
|---|---|---|---|
| scheduler weekly batch (`run_batch`) | `live` | `live:…` | `batch-<date>` |
| scheduler Monday forward cohort (`backtest.py` subprocess) | `live` | `monday_t1_fwd:…` | `monday_t1_fwd` |
| `backtest.py --trace` | `backtest` | `<backtest_id>:…` | `<backtest_id>` |
| `run_longterm_agent` | `dev` | `manual:…` | `manual-<date>` |

The trace id derives from the cohort **date**. A grader can therefore attach outcome scores later
with `tracing.run_trace_id(...)` and needs no new column. `LT_TRACE=false` turns tracing off.
Without `LANGFUSE_*`, tracing is a no-op. A down Langfuse never fails a run, because the SDK
exports in a background thread.

The trace keeps every prompt in full. Graph state keeps only article ids, not their text: a
mask replaces `news_corpus` on every node span, otherwise the corpus would be stored once per
node.

## Outcome scores

The daily grading step ends with `longterm_agent/label_traces.py`. It writes labels from data
that arrived after a run into Postgres `trace_labels`, then mirrors them here as scores (source
`API`). Langfuse is the mirror, not the store: a fresh project gets every label on the next run.

- **Where they land.** A label of the whole decision (`excess_4w`, `hit_4w`, `excess_stopped`,
  `worst_position_4w`, …) goes on the trace. A label of one node goes on that node's
  observation, e.g. `shift_detection.outcome`. Each score name has a score config, created on
  the first run.
- **Score time.** A score is stamped when its outcome became knowable (`label_available_at`),
  not when it was sent, so score charts read as "what we learned when". Langfuse keeps a score
  id's first timestamp on upsert.
- **Runs older than tracing.** A labeled run with no trace gets one rebuilt from
  `longterm_run_snapshots`, under the id the live run would have had and at its real time.
  Rebuilt traces carry the tag `rebuilt:snapshot`; node timings inside them are synthetic.
  `python -m longterm_agent.label_traces --rebuild-all` also rebuilds the unlabeled (NEUTRAL)
  runs, so the whole history can be browsed. That is about 960 traces built from 26 MB of
  snapshot JSON (2026-10-29), before ClickHouse compression.
- **Do not delete and re-send a score id within minutes.** Deletion is asynchronous and can
  remove the re-created score (seen on 4.70.1).

## Operations

- **Upgrade.** Change the pinned tags, run `docker compose pull && docker compose up -d`, and
  back up first. v4 has no automatic downgrade path once its schema migrations have run.
- **Backup.** Postgres holds users, keys, datasets, prompts and score configs:
  `docker compose exec -T postgres pg_dump -U postgres postgres | gzip > /root/backups/langfuse-$(date +%F).sql.gz`.
  ClickHouse holds traces and scores. They matter less, but export scores before risky changes.
- **Raw events.** MinIO keeps every ingested event (~1.3 MB per traced agent run). Langfuse
  re-reads them only to retry failed ingestion or to replay, so they expire after 30 days,
  as on Langfuse Cloud. Set once after the first start (done on the server 2026-10-28):
  ```bash
  docker compose exec -T minio sh -c 'mc alias set local http://localhost:9000 minio "$MINIO_ROOT_PASSWORD" && mc ilm rule add --expire-days 30 --prefix events/ local/langfuse'
  docker compose exec -T clickhouse sh -c 'clickhouse-client --user clickhouse --password "$CLICKHOUSE_PASSWORD" -q "ALTER TABLE blob_storage_file_log MODIFY TTL created_at + INTERVAL 30 DAY DELETE"'
  ```
- **Retention.** Self-hosted retention policies for traces are an Enterprise feature. Old
  backtest traces are deleted by hand, so trace backtests only when you need to inspect them.
- **Footprint on the server** (2026-10-28, idle): RAM ≈1.6 GiB (web 0.67, worker 0.38,
  ClickHouse 0.41, MinIO 0.08, Postgres 0.04, Redis 0.01); CPU ≈14% of one core. Images
  4.0 GB, volumes 85 MB, growing by about 30 MB a week at the live cadence.
- **Never** copy `.env` off the host or into an image. `deploy/ship_image.sh` refuses images
  that contain one.
