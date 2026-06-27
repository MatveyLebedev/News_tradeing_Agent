# Disk & Log Safety (REQUIRED on every server)

A server disk filled to 95% once because the **Postgres container's stdout log**
grew to **18 GB** — Docker's `json-file` driver has **no size limit by default**,
and Postgres/Neo4j are started outside the compose files (so the per-service
`logging:` caps didn't apply to them). This runbook prevents a recurrence.

Two independent layers can eat disk; both must be bounded:

| Layer | What | How it's bounded |
|---|---|---|
| Container stdout logs | `/var/lib/docker/containers/*/*-json.log` | **global `daemon.json` cap** (below) — covers *every* container |
| App logs in the DB | `news_parser_log`, `longterm_agent_log` tables | row caps in code (`LOG_MAX_RECORDS`, oplog trim) |
| Postgres server log | what Postgres itself writes to stdout | quiet logging config (below) |

---

## 1. Global Docker log cap — do this once per server, FIRST

Catches every container no matter how it's started (compose **or** `docker run`),
including `postgres`, `neo4j`, `3x-ui`, etc.

```bash
# install the repo's daemon config
sudo cp deploy/daemon.json /etc/docker/daemon.json
sudo systemctl restart docker          # restarts the daemon (containers restart too)

# the cap applies to NEWLY CREATED containers — recreate existing ones:
docker compose up -d --force-recreate
# and any standalone containers (neo4j, postgres, 3x-ui) — recreate with the
# run commands below, which now also pass --log-opt.
```

Cap = `50m × 5 files` ⇒ at most ~250 MB of logs per container, ever.

## 2. Quiet Postgres logging (root cause of the 18 GB)

Run the `postgres` / `pgvector` container with logging turned down. Append these
flags to the `docker run` (or `command:` in compose):

```
-c log_statement=none
-c log_min_duration_statement=1000     # log only queries slower than 1s
-c log_min_messages=warning
-c log_connections=off
-c log_disconnections=off
```

If it's already running noisy, check what it's spamming before changing:
`docker logs --tail 50 postgres`.

## 3. Per-service caps in compose (defense in depth)

Every service in `docker-compose.yml` already has:
```yaml
logging:
  driver: "json-file"
  options: { max-size: "10m", max-file: "3" }
```
Keep this block on **every** service you add. The global cap (step 1) is the
backstop for anything started outside compose.

## 4. Standalone `docker run` — always pass log opts

Neo4j and any other `docker run` container must include:
```
--log-driver json-file --log-opt max-size=50m --log-opt max-file=5
```
(See README.md for the full `stock-fetcher` / `news-fetcher` run command — it
already includes these.)

## 5. Monitoring + periodic hygiene (cron on the server)

```bash
# weekly: drop build cache older than 7 days (build cache is invisible to
# `docker image prune` and grew to tens of GB on the dev machine)
0 4 * * 0  docker builder prune -af --filter until=168h

# daily: alert if / is over 85%
0 * * * *  [ "$(df --output=pcent / | tail -1 | tr -dc 0-9)" -gt 85 ] && \
           echo "disk >85% on $(hostname)" | logger -t disk-watch
```

---

## Emergency recovery (disk already full)

```bash
df -h /                                              # confirm
du -xhd1 /var/lib/docker | sort -rh | head           # find the hog
du -sh /var/lib/docker/containers/*/*-json.log | sort -rh | head   # usually here

# safe, immediate reclaim (logs only — no data/images/volumes touched):
truncate -s 0 /var/lib/docker/containers/*/*-json.log
journalctl --vacuum-size=200M
df -h /

# then apply steps 1–2 so it can't refill, and restart anything that got
# OOM/disk-killed (look for "Exited (137)"):
docker start postgres neo4j bloomberg_news_fetcher 2>/dev/null
docker compose up -d
```

> Note: `docker image prune -f` will report `0B` here — the space is in
> **container logs and build cache**, not dangling images. Don't run
> `docker volume prune` (it can delete the Postgres/Neo4j data).
