# Long-Term Agent — Deployment (Phase 6)

Two long-running services, same Docker/compose pattern as the rest of the repo:

| Service | Container | What it does |
|---|---|---|
| `longterm-agent` | `longterm_agent` | **Weekly** full-pipeline batch across the universe → persists results, grades matured cohorts (daily), pushes Telegram alerts for new/flipped signals. |
| `longterm-stats-bot` | `longterm_stats_bot` | Read-only **Telegram bot**: `/stats /signals /trades /errors /ticker`. |

Both build from one image (`longterm_agent/Dockerfile`); the bot reuses it with a different `command:`.

---

## 1. Prerequisites

- The shared Postgres (`trade_agent`) and Neo4j the other services already use.
- News must be flowing into `news` and the graph (the `news-fetcher` service).
- **One-time data backfills** (heavy; run by hand once, then the weekly fetchers top up):

```bash
cd bloomberg_news_fetcher
SEC_EDGAR_USER_AGENT="YourName you@email.com" python fundamentals_fetcher.py   # EDGAR PIT + yfinance
python daily_price_fetcher.py --period 2y                                       # daily bars + SPY
```

Make sure `FETCH_FUNDAMENTALS=true` in `.env` so the `news-fetcher` keeps fundamentals current
weekly (Saturday 03:00 ET).

**Daily price bars are a separate job and are NOT gated by that flag** — grading, `/open`'s
vs-SPY leg and the batch freshness preflight all read `stock_prices_daily` on a market clock:

```ini
DAILY_BARS_FETCH_ENABLED=true    # default
DAILY_BARS_FETCH_TIME=20:30      # ET, post-close; clear of options 17:30 / macro 18:30 / filings 19:30
DAILY_BARS_PERIOD=1mo            # window > cadence, so a dropped or missed bar heals itself
```

They rode along with the weekly fundamentals job until 2026-07-28. That cost two things
worth knowing about: a cohort opened on Sunday had no benchmark entry bar until the
*following* Saturday (so half the open book showed no vs-SPY), and a bad fetch sat
uncorrected for days because nothing re-read it.

---

## 2. Configure `.env`

Add (see `.env.example` → "Long-Term Agent — Ops / Phase 6"):

```ini
# Schedule (ET). Put the batch AFTER the weekly fundamentals fetch (Saturday 03:00 default).
LT_BATCH_WEEKDAY=sunday
LT_BATCH_TIME=06:00
LT_GRADE_TIME=07:00
LT_RUN_ON_START=false          # set true once to produce signals immediately on first boot
# LT_UNIVERSE=NVDA,AAPL,MSFT   # optional; default = TOP_50_TICKERS
LT_ALERT_ON_ERROR=true

# Telegram — the stats bot needs its OWN token (see warning below)
LT_TELEGRAM_BOT_TOKEN=999999:XYZ-...     # @BotFather — a SECOND bot, just for this agent
LT_TELEGRAM_CHAT_ID=-1001234567890       # where alerts are pushed (you/channel)
TELEGRAM_ALLOWED_USER_IDS=11111111       # who may query the stats bot (comma-sep)
```

> ⚠️ **Use a separate bot token.** Telegram allows only **one** poller per bot token.
> This repo's existing `telegram-bot` already polls `TELEGRAM_BOT_TOKEN`, so if the stats
> bot reuses it you get `telegram.error.Conflict: terminated by other getUpdates request`
> (and it disrupts the existing bot). Create a **second** bot with @BotFather and set
> `LT_TELEGRAM_BOT_TOKEN` / `LT_TELEGRAM_CHAT_ID`. (Verified in local Docker testing.)
> If you don't run the existing `telegram-bot`, reusing `TELEGRAM_BOT_TOKEN` is fine.

**Telegram setup (5 min):**
1. Message **@BotFather** → `/newbot` → copy the token → `LT_TELEGRAM_BOT_TOKEN`.
2. Get your chat id: message **@userinfobot** (personal) or add the bot to a channel and read the id from `https://api.telegram.org/bot<TOKEN>/getUpdates`. Put it in `LT_TELEGRAM_CHAT_ID`.
3. Put your numeric user id in `TELEGRAM_ALLOWED_USER_IDS` so only you can query the bot.

If you omit the Telegram vars the agent still runs — alerts just print to the container log
(scheduler stays up; the stats-bot service needs a token to start).

---

## 3. One-time DB tables

The agent **auto-creates** its tables on first use (`longterm_agent_results`, `shift_library`,
`bucket_summary_cache`, `longterm_agent_log`) and idempotently adds the Phase-6 columns
(`traded`, `risk_flags`). No manual SQL needed. To pre-create explicitly:

```bash
psql "$DATABASE_URL" -f longterm_agent/create_results_table.sql
psql "$DATABASE_URL" -f longterm_agent/create_oplog_table.sql
psql "$DATABASE_URL" -f longterm_agent/create_shift_library_table.sql
psql "$DATABASE_URL" -f longterm_agent/create_bucket_cache_table.sql
```

---

## 4. Build & run

```bash
# build the image (context = repo root)
docker compose build longterm-agent

# start both services
docker compose up -d longterm-agent longterm-stats-bot

# logs
docker compose logs -f longterm-agent
docker compose logs -f longterm-stats-bot
```

On start the scheduler logs the cadence and (if Telegram is configured) sends
`🟢 Long-term agent scheduler started`. The bot replies to `/stats`.

### Smoke-test before waiting a week
```bash
# run ONE batch now, then keep scheduling (also: set LT_RUN_ON_START=true once)
docker compose run --rm longterm-agent python -u -m longterm_agent.auto_longterm_agent --now

# or just a couple tickers, no alerts
docker compose run --rm longterm-agent \
    python -u -m longterm_agent.batch_run_longterm --tickers NVDA AAPL --no-alert

# grade matured cohorts on demand
docker compose run --rm longterm-agent python -u -m longterm_agent.auto_longterm_agent --grade-now
```

---

## 5. What you'll see in Telegram

**Pushed automatically (alerts):**
- 🟢/🔴 per **new or flipped** directional signal — ticker, direction, conviction, horizon, thesis, and whether it's a `TRADE` (passed the risk gate) or just a `signal`.
- 📊 a weekly batch summary (runs / trades / signals / no_data / errors).
- ⚠️ an error alert per failed ticker (if `LT_ALERT_ON_ERROR=true`).

**On demand (stats bot):**
- `/stats` — overview: signal counts, **trade win-rate vs SPY**, error health.
- `/signals [N]` — latest directional calls (with realized 4w excess once graded).
- `/trades` — actionable-trade outcomes (win rate, mean/median excess vs SPY, pending count).
- `/errors [N]` — recent errors from the operational log.
- `/ticker NVDA` — latest verdict + thesis for one ticker.

> **signal vs trade:** a *signal* is any LONG/SHORT verdict; a *trade* is a signal that
> passed the risk gate (`traded=TRUE`) — the actionable subset. A trade is "graded" once
> its 4-week horizon elapses (win = beat SPY).

---

## 6. Operations

- **Scheduling:** weekly batch `LT_BATCH_WEEKDAY`/`LT_BATCH_TIME` ET; grading runs daily at
  `LT_GRADE_TIME` and again right after each batch. The container keeps running (`restart:
  unless-stopped`).
- **Where the numbers live:** `longterm_agent_results` (one row per ticker/as_of, with
  `traded`, `conviction`, graded `fwd_return_4w`/`benchmark_return_4w`) and
  `longterm_agent_log` (run events + errors, retention-capped at `LT_LOG_RETENTION`, default 20k).
- **Health check:** `docker compose run --rm longterm-agent python -m longterm_agent.stats`
  prints the same dashboard the bot serves.
- **Backtests / library backfill** (optional, see `README.md` §4): `backfill_shift_library.py`,
  `grade_shifts.py`, `backtest_report.py`, `full_signal_backtest.py`.

## 7. Validation note

The strategy is **not yet validated** — backtests so far are single-regime noise (README §7).
Treat the first weeks as forward paper-testing: signals accumulate in `longterm_agent_results`,
and the `/trades` win-rate only becomes meaningful after a quarter of graded cohorts.

## 8. Data-layer status (audited 2026-07-10; Modification Plan Phase 0)

What writes what, on which schedule:

| Data | Writer | Schedule | Status 2026-07-10 |
|---|---|---|---|
| `news` (+ graph) | `news-fetcher` container | every 5 min | ✅ fresh (max published = yesterday) |
| intraday bars → `historical_data_<ticker>` (120 tables) | same container (`FETCH_PRICES=true`) | every 5 min | ✅ live. NOTE: `stock_prices` is a **dead legacy table** (0 rows) — do not read it |
| `stock_prices_daily` (incl. **SPY** benchmark) | weekly job in `news-fetcher` → `fetch_all_daily` | Saturday 03:00 ET (`FUNDAMENTALS_FETCH_*`) | ✅ fresh through 2026-07-09 after manual backfill |
| `fundamentals` (EDGAR PIT + yfinance snapshots) | same weekly job | Saturday 03:00 ET | ✅ last run 2026-07-04 (on schedule) |

Incident record (why SPY mattered): the weekly job passed bare `TOP_50_TICKERS` to
`fetch_all_daily`, and SPY is **not** in that list — so the grading benchmark silently froze
at 2026-06-15 while the universe kept updating; all outcome grading (X-vs-SPY) was blocked.
Fixed 2026-07-10: `daily_price_fetcher.DEFAULT_TICKERS = TOP_50 + BENCHMARK_TICKERS (SPY)`,
weekly top-up widened `5d → 1mo` (self-heals a missed week), connection retry added.
**The fix lives in the image — rebuild/redeploy `news-fetcher` before the next Saturday run,
or SPY freezes again.** Live-table grades were audited: 0 rows were graded against frozen
prices (contamination was confined to the wf_2026q2 backtest report, already marked ESTIMATE).

Known cosmetic issue: `SQ` (Block → ticker now XYZ) is delisted and fails in every fetch;
remove/replace it when the universe list is next touched (label-affecting — bundle with a
detection release).

## 9. Security hardening runbook (Phase 2)

Current exposure: Postgres and Neo4j listen on public non-default ports. Non-default
ports are **not** access control. Do these on the server, in order:

1. **Network**: either WireGuard/Tailscale (preferred — DBs then bind to the VPN
   interface only), or at minimum a firewall allowlist:
   ```bash
   ufw default deny incoming
   ufw allow OpenSSH
   ufw allow from <YOUR_HOME_IP> to any port <PG_EXT_PORT> proto tcp
   ufw allow from <YOUR_HOME_IP> to any port <NEO4J_EXT_PORT> proto tcp
   ufw enable
   ```
   (Containers on the host reach the DBs via the docker network — the allowlist only
   affects off-server clients.)
2. **SSH**: create a non-root deploy user; disable root + password login:
   ```bash
   adduser deploy && usermod -aG docker,sudo deploy
   # copy your key: ssh-copy-id deploy@<SERVER_IP>
   # /etc/ssh/sshd_config: PermitRootLogin no, PasswordAuthentication no → systemctl restart sshd
   ```
3. **Rotate** everything that ever appeared in git history or tracked files: DB password,
   Neo4j password, Telegram tokens, any API keys. The server IP itself was in tracked
   docs until 2026-07-10 (now scrubbed); history still contains it — rotation + firewall
   is the practical mitigation (history rewrite optional).
4. Verify from an *unlisted* network: `psql`/`cypher-shell` connection attempts must
   time out; SSH as root must be refused.
