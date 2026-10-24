# Operations — news & market-data fetcher

> Runbook moved here from the root README, which is now the project overview. Agent-level
> run commands live in [`longterm_agent/README.md`](../longterm_agent/README.md#4-running)
> and [`longterm_agent/DEPLOY.md`](../longterm_agent/DEPLOY.md).

News and market-data fetcher, saving to PostgreSQL. Two clocks, not one: the main loop
runs every `FETCH_INTERVAL_MIN` minutes (default 5) and writes news plus 1-minute bars,
while the daily OHLCV top-up runs at `DAILY_BARS_FETCH_TIME` (default 22:30 ET) and again
at `DAILY_BARS_RETRY_TIME` (default 06:15 ET, before the 07:00 grader). Not 20:30: just
after the 20:00 ET extended-hours close Yahoo serves the day's bar with NaN OHLC, the NaN
guard drops it, and bar D used to reach the table only at D+1 20:30 (2026-09-02 → 10-23).
A server `.env` that still pins `DAILY_BARS_FETCH_TIME=20:30` overrides the new default.

## Remote Access & Deployment

### 0. Disk & Log Safety — do this FIRST on any new/rebuilt server

Docker's default `json-file` log driver has **no size limit**, and Postgres/Neo4j
run outside compose, so a single uncapped container log once grew to 18 GB and
filled the disk. Before deploying, install the global log cap:

```bash
sudo cp deploy/daemon.json /etc/docker/daemon.json   # caps EVERY container's logs
sudo systemctl restart docker
docker compose up -d --force-recreate                # recreate so the cap applies
```

Full procedure, Postgres logging config, monitoring cron, and emergency recovery:
**[deploy/DISK_SAFETY.md](../deploy/DISK_SAFETY.md)**. Every `docker run` below already
passes `--log-opt max-size`; keep it on any container you add.

### 1. Login to Remote Server
To access your remote server via SSH:
```bash
ssh <deploy-user>@<SERVER_IP>
```

### 2. Build and Ship to the Server

**Images are never pushed to a public registry.** Do not run `docker push` for this
project — see the 2026-08-11 security incident (write-up kept out of the public repo) for
what that cost. `docker push` creates a new repository as **public by default**, with no
prompt and no warning.

Delivery goes over the SSH channel that already exists for ops:

```bash
# 1. Build. For the server (x86_64) always use --platform linux/amd64.
docker build --platform linux/amd64 \
  -t stock-fetcher:$(date +%Y-%m-%d) \
  -f bloomberg_news_fetcher/Dockerfile.news .

# 2. Sanity-check the contents. Dockerfile.news COPYs named files rather than `COPY . .`,
#    so a newly added module is silently absent until something imports it.
docker run --rm stock-fetcher:$(date +%Y-%m-%d) \
  python -c "import price_fetcher as p; print(len(p.DEFAULT_TICKERS), 'tickers')"

# 3. Ship. Refuses to send an image containing .env or *.bak, streams it to the server,
#    and verifies the loaded image id matches the local one.
DEPLOY_HOST=root@<SERVER_IP> deploy/ship_image.sh stock-fetcher:$(date +%Y-%m-%d)
```

Note the tags are **local names only** (`stock-fetcher:…`, not `<user>/stock-fetcher:…`).
An unqualified name cannot be pushed to Docker Hub by accident.

Date-tag every build rather than reusing `:latest`. The server keeps previous tags, and
that is the whole rollback story now that there is no registry to pull an old image from.

### 3. Start Application (Remote/Local)
Navigate to the project directory and use Docker to start the services:


#### Using Docker Run (Single Command)
The container is named **`bloomberg_news_fetcher`**, not `stock-fetcher` — `stock-fetcher`
is the *image*. Stopping the wrong name is a no-op that leaves the old container running
while you believe you redeployed.

Credentials come from `--env-file /root/trade_agent/.env`, which is the single source of
truth on the server. Do NOT pass secrets as `-e` literals: they end up in shell history
and in `docker inspect`, and a container bound to a private copy stops tracking edits to
the canonical file. Only the four settings absent from that file are passed explicitly.

Run these **on the server**, after `ship_image.sh` has put the image there. `TAG` is the
date tag you built with; the previously running tag stays on the server as the rollback.

```bash
TAG=2026-08-11        # the tag you just shipped

# 1. Note what is running now — this is what you roll back to
docker inspect bloomberg_news_fetcher --format 'currently: {{.Config.Image}}'

# 2. Confirm the SHIPPED image carries the change, before touching a working container
docker run --rm stock-fetcher:$TAG \
  python -c "import price_fetcher as p; print(len(p.DEFAULT_TICKERS), 'tickers')"

# 3. Recreate
docker stop bloomberg_news_fetcher; docker rm bloomberg_news_fetcher
docker run -d \
  --name bloomberg_news_fetcher \
  --restart unless-stopped \
  --env-file /root/trade_agent/.env \
  -e TZ=America/New_York \
  -e FETCH_INTERVAL_MIN=5 \
  -e FETCH_PRICES=true \
  -e FETCH_FULL_TEXT=false \
  -e VERBOSE=true \
  --log-driver json-file \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  stock-fetcher:$TAG

# 4. Verify — within one FETCH_INTERVAL_MIN the log must report the FULL universe
#    (132 = 120 companies + 12 benchmark ETFs). A count of 120 means a stale image:
#    ETFs joined the minute fetch on 2026-07-30 and are what SPY-netted intraday work
#    depends on.
docker logs bloomberg_news_fetcher --since 10m 2>&1 | grep "Price data rows inserted"
```

Rollback, if step 4 disappoints — re-run step 3 with the tag from step 1:
```bash
docker stop bloomberg_news_fetcher; docker rm bloomberg_news_fetcher
# …same docker run, with the previous stock-fetcher:<date> as the image
```

Prune old tags on the server occasionally (`docker image ls stock-fetcher`), but keep at
least the previous one — with no registry, the server's local tags are the only rollback.

## Local Usage Summary

To use this repository locally, ensure you have **Docker** and **Docker Compose** installed.

1. **Setup**: Clone the repository and navigate into it.
2. **Launch**: Run `docker compose up -d`. This starts both the PostgreSQL database and the fetcher service.
3. **Monitor**: Check logs to ensure data is being fetched:
   ```bash
   docker compose logs -f price-fetcher
   ```
4. **Manual Trigger**: If you don't want to wait for the schedule, run a manual fetch:
   ```bash
   docker compose exec price-fetcher python auto_price_fetcher.py --once
   ```
5. **Stop**: To stop all services:
   ```bash
   docker compose down
   ```

## Configuration
Edit `docker-compose.yml` environment variables to customize:
- `FETCH_TIME`: Daily run time (default: 22:00 ET)
- `FETCH_INTERVAL`: Data interval (default: 1m)
- `FETCH_PERIOD`: Data period (default: 1d)
- Database credentials (DB_USER, DB_PASSWORD, etc.)

## Files
- `Dockerfile`: Container definition
- `docker-compose.yml`: Service orchestration (DB + Fetcher)
- `requirements.txt`: Python dependencies
- `price_parser.py`: Core logic for fetching prices
- `auto_price_fetcher.py`: Scheduling and execution logic
