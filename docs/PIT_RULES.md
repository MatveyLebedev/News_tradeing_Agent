# Point-in-time discipline — standing rules

The engineering contract that keeps lookahead out of this codebase. `WALK_FORWARD_PROTOCOL.md`
governs how a backtest is *read*; this governs how the code that produces it is *written*.
The one-off investigation that produced these rules is `PIT_AUDIT_2026-07-19.md`; the fixes
it applied are summarised at the bottom.

Read this before adding a reader, a tool, or a feature that touches `as_of`.

---

## 0. The rule behind the rules

> Every input must answer **"could the decision at `as_of` have known this?"** — not
> "is this dated before `as_of`".

Those two questions differ far more often than they look. A filing dated today is not
knowable at 06:30. An event that happened in May is not knowable in May if the only article
reporting it ran in July. A story's article counter says how big the narrative *became*.
A 1-day price reaction to yesterday's news has not finished printing. Every leak found in
the 2026-07-19 audit was a case where the timestamp check passed and the knowability check
would not have.

Corollary: **a backtest is a claim about what you would have done, not about what happened.**
Anything that makes the replay differ from a live run at that moment is a defect, even when
it is not literally "future data" — a live network call is a defect for the same reason.

---

## 1. The clocks — which cutoff for which input

Helpers live in `longterm_agent/tools/pit.py`. Never hand-roll a comparison.

| Input class | Rule | Use |
|---|---|---|
| Daily price bars | Visible after the session closes (~16:00 ET) | `daily_bar_cutoff(as_of)` |
| Third-party document dates — EDGAR `filed_date`, 8-K/Form-4, earnings report dates | Same session-close rule (market-moving filings cluster post-close) | `document_cutoff(as_of)` |
| Our own snapshots — yfinance / FMP / options fetches | Visible from the moment **we** pulled it | `ingest_cutoff(as_of)` vs `created_at` / `fetched_at` |
| Article publication | Full precision is available — use it | `published <= as_of` in SQL |
| Macro series (VIX, HY-OAS, DGS10, oil) | Daily closes → session-close rule | `daily_bar_cutoff(as_of)` |
| Graph events | **Two clocks** — see §2 | `_known_by()` + `e.date <= $pub` |
| Derived counters (e.g. `Story.article_count`) | Recompute as-of; never read the stored value | count in-date members |
| Realized outcomes (`move_*`, forward returns) | Grading only, or maturity-masked — see §4 | `MOVE_MATURITY_DAYS` |

**Why snapshots are different.** A filing is a third-party event with a publication time we
approximate by session close. A snapshot is *our own action* with an exact timestamp. Using
the session rule on snapshots would break live: the Sunday 06:00 batch must see the Sunday
05:00 fundamentals fetch. Using the ingestion rule on filings would be meaningless — we
backfill them years later.

---

## 2. The graph has two clocks

`e.date` is when the event **happened**. It is necessary and *not sufficient*.

```cypher
-- WRONG: an article published in July can describe a probe that started in May
WHERE e.date <= datetime($pub)

-- RIGHT: it also has to have been reported by then
WHERE e.date <= datetime($pub) AND <_known_by("e")>
```

`_known_by()` (`news_agent/tools/graph_tools.py`) expands to an `EXISTS` on
`SOURCED_FROM.published_at`. NULL passes — ~4% of edges predate the property, and dropping
them would silently shrink every corpus.

This is not a marginal correction: on the live graph the second clock removes **half** the
events a 30-day ticker window returned.

---

## 3. Enforce in code, never in a prompt

A model instruction is not a control. `run_graph_query` still *asks* for a `$as_of` gate —
because a gated query is cheaper and returns a more useful set — but it also drops
post-`as_of` rows and strips `move_*` before the model sees anything. If the only thing
standing between you and lookahead is a sentence in a docstring, you have no control.

The same applies to tool docstrings that say "point-in-time": they describe intent. The
canary is what verifies it.

---

## 4. Realized outcomes belong to grading

Forward returns, `move_1d`, exit prices, adverse excursion — these exist to *score* a
decision, never to inform one. Keep them in `grade_*.py` / `targets.py`.

Where an outcome is genuinely useful as context (the intraday-reaction bridge), it must be
**maturity-masked**: an event published shortly before `as_of` carries a reaction that
extends past it. Mask at the read, where `as_of` is known, so no downstream formatter can
forget (`mature_move_ids`). Immature events stay visible but ungraded — withhold the number,
not the row.

---

## 5. Degrade, don't reach

When a point-in-time source has no answer, return `Unknown` / `{"available": False}` and let
the caller cope. Do **not** fall back to a live or current-valued source. Two consequences
worth stating plainly:

- **No network calls on a replayable path.** They leak today's state and destroy
  reproducibility (the LLM cache keys on prompt content, so a drifting tool result silently
  changes the run). `resolve_sector_industry` is the pattern: static map → as-of snapshot →
  `Unknown`, with no third option.
- **No "current" fields.** A live counter, a `.info` blob, "latest" anything — if the value
  changes when you re-run an old cohort, it does not belong in the decision path.

---

## 6. Time zones — the three clocks that bite

Mixing them raises `TypeError` at best and silently shifts a cutoff by 4–5 hours at worst.
This has already cost one production tool (see the record below).

| Source | Kind |
|---|---|
| `as_of` | naive, **ET** wall clock |
| `created_at` / `fetched_at` (Postgres `NOW()`, UTC session) | naive, **UTC** |
| `agent_results.news_published` | **tz-aware** |
| `news.published` | naive |

Normalise through `pit.as_moment()` (→ naive ET) or `pit.ingest_cutoff()` (→ naive UTC).
Never compare two raw datetimes from different rows of that table.

---

## 7. Adding something new — what to do

| You are adding… | Then you must… |
|---|---|
| A reader with an `as_of` argument | Pick a cutoff from §1; add a case to `test_leakage_canary.py` |
| A deep-analysis tool | Confirm `test_deep_tools_canary.py` exercises it (add args if it needs them); it must **degrade, not raise** |
| A Cypher query | Both clocks (§2); no raw `move_*` |
| A derived counter or aggregate | Compute it as-of; never read a stored live counter |
| A feature flag | Default OFF, prompts byte-identical when off (LLM-cache keys) |
| A grading target | Keep it in the grading path; it may see the future by construction |

### Review checklist

- [ ] No bare `<= as_of.date()` on a decision path — a named cutoff is used
- [ ] Graph queries gate on both `e.date` and `_known_by()`
- [ ] No live network call reachable from a replay
- [ ] No stored "current" counter read as if it were historical
- [ ] Realized outcomes are either grading-only or maturity-masked
- [ ] Datetimes normalised before comparison (§6)
- [ ] Canary covers the new surface, and fails if it regresses
- [ ] Version bumped per §8 if results move

---

## 8. When a PIT fix changes results

A PIT fix is a **measurement** change, and it is not free:

1. Bump `PIPELINE_VERSION`. Bump `DETECTION_VERSION` only if shift membership/labels can
   move (map-model sentiments and the deterministic metrics are what decide that).
2. Results before and after are **not like-for-like** — use a fresh `backtest_id`.
3. Under the burned-grid rule, re-running an old window after a fix can only **demote** a
   prior verdict, never re-accept it.
4. Say so in `BACKTEST_LEDGER.md`, including which prior entries the fix calls into question.

The honest framing: a leak found today means yesterday's numbers were optimistic, and the
correction is not optional just because it is inconvenient.

---

## 9. Tests that must stay green

| File | Guards |
|---|---|
| `tests/test_pit_cutoffs.py` | The cutoff helpers and the date scanner (pure) |
| `tests/test_leakage_canary.py` | Readers, at midnight **and** 06:30 as_of stamps |
| `tests/test_deep_tools_canary.py` | Every deep tool's output scanned for future dates; raising = failure |
| `tests/test_graph_query_tool.py` | Raw-Cypher read-only + PIT + parseable output contract |
| `news_agent/tests/test_tools.py` | Sector resolution stays offline and deterministic |

The deep-tool canary is deliberately generic — a newly added tool is picked up
automatically. **Do not add an "it couldn't run, so it can't leak" excuse to it.** That
clause is exactly what hid a broken tool once: a crashing tool is a tool whose PIT behaviour
is unverified.

---

## 10. Record of fixes — 2026-07-19

Detail and evidence in `PIT_AUDIT_2026-07-19.md`. `PIPELINE_VERSION` 0.8.0 → 0.9.0;
detection labels untouched.

**Decision-path leaks closed**

| # | Was | Now |
|---|---|---|
| F1 | Raw Cypher trusted the model to write the `$as_of` gate | Post-`as_of` rows dropped in code, `move_*` stripped, `pit_note` returned |
| F2 | `move_1d` attached to events published just before `as_of` | Immature events excluded at the read (`mature_move_ids`) |
| F3 | Events passed on `e.date` alone | Second clock: must be reported by `as_of` (removes ~half the rows) |
| F4 | `Story.article_count` — a live, and lossy, counter | Counted as-of from in-date sourcing articles |
| F5 | `<= as_of.date()` on EDGAR / filings / insider / FMP / snapshots | `document_cutoff` / `ingest_cutoff` — this was a **post-entry** leak under `open_d0` |
| F5b | `_new_stories` ranked by future story size | Ranked by as-of size |
| — | `macro_series` read the as_of day's own closes | `daily_bar_cutoff`, cache keyed by cutoff |
| — | `_regime()` window ended at the as_of close | Ends at the last completed session |
| — | Sector/industry from live `yf.Ticker().info` in replay | Offline `resolve_sector_industry` (static map → as-of snapshot → `Unknown`) |

**Measurement honesty**

| # | Change |
|---|---|
| F6 | `full_signal_backtest.py` moved off the retired close-to-close convention to `open_t1` |
| F7 | Analog windows anchored at `entry_date + horizon`, matching where the trade actually is |
| F9 | Rows past maturity with no outcome print as `STALE-UNGRADED` instead of vanishing (halt/delisting is both the likeliest cause and the worst outcome) |
| F10 | Cohort-block bootstrap CI beside the per-trade one — ~5× wider on correlated cohorts, so every prior per-trade CI in the ledger is too narrow |

**Regression caught by the re-check.** The F2 fix compared a tz-aware column against a naive
`as_of` and took `get_intraday_reactions` down on every call; the canary had excused it as
"couldn't run". Both are fixed — and rule §9 exists because of it.

**Still open** (both only in the consensus feature, `LT_CONSENSUS_IN_VERDICT=false` by
default): the `_yf_hist_future` 45-day reporting-lag buffer, and the FMP retro path that
rests on the provider's claim that per-report estimates are frozen at report time.

Deferred by decision, tracked in the audit doc as D1–D5: LLM knowledge-cutoff gate (blocking
for any pre-2026 backtest window), transaction costs, EDGAR acceptance-datetime, the
`valuation_pit` split mismatch, and universe-as-of survivorship.
