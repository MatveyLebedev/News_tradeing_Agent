# Benchmark convention — how a trade is scored against the market

Status: **standing rule**, adopted 2026-07-31. Applies to every backtest, holding curve and
ledger entry from this date. Supersedes the implicit beta-1 arithmetic used before it.

---

## The rule

> A trade is scored against a **beta-matched short of SPY**, never a flat 1:1 short.
>
> ```
> excess = side × (stock_return − beta × spy_return)
> ```
>
> `beta` is the stock's OLS beta to SPY over the **180 daily bars strictly before the entry
> date** (`longterm_agent/beta.py`, minimum 30 observations). It is a property knowable at
> decision time; grading may see the future by construction, but the hedge ratio must not.

`side` is `+1` for LONG and `−1` for SHORT. When beta cannot be fitted (too short a price
history) the trade stays in the sample at `beta = 1.0` and the count is reported, so the
denominator never changes silently.

## Why

A 1:1 short of SPY prices every name as if it moved with the market one-for-one. It does
not, and the error is not symmetric noise — it is a systematic tilt that tracks the book's
composition:

* In `monday_t1_pit`, PEP and KO fitted at beta ≈ −0.03 to SPY. Each carried a mechanical
  ~−5pp before any judgement was involved (`longterm_agent/beta.py`).
* In `intraday_curve34`, the low-burn book fitted at **median beta 0.48** (IQR 0.02–1.10,
  60% of trades below 0.8). The book is defensive because it was selected by burn intensity
  and sector balance, which pushed weight away from Technology and Financial Services — the
  high-beta corners. A 1:1 hedge left ≈0.42 beta units of unintended **short** market
  exposure per trade.

The tilt lands on the strategy and on the always-long control by *different* amounts,
because the control is direction-blind while the strategy is roughly direction-balanced.
That makes the comparison that decides most experiments — *does the signed book beat its own
direction-blind control?* — sensitive to a parameter nobody chose.

## What this does NOT change

* **The agent's decisions.** This is a reporting convention. No gate, threshold or prompt
  reads beta. Same class of change as the sector-relative benchmark.
* **The intraday leg of a holding curve.** Inside the entry session there are no ETF minute
  bars, so the hedge is an equal-weight basket of the sweep's own universe. A beta measured
  against SPY does not describe that hedge, so the intraday ratio stays 1:1. Only the `eod`
  leg — where a real SPY series exists — is beta-matched.
* **Raw and EW modes.** `--benchmark none` and `--benchmark ew` are unaffected.

## Where it is implemented

| surface | how |
|---|---|
| daily grading | `longterm_agent/backtest.py::grade` → `beta`, `excess_beta` columns |
| ledger reporting | `primary_metric: excess_beta` in the provenance knobs |
| holding curves | `longterm_agent/holding_curve_lt.py`, **default on**; `--no-beta-adjust` restores beta=1 |

`--no-beta-adjust` exists only to reproduce numbers published before this document. It is
not a valid convention for new results, and the harness prints a warning when it is used.

## Results published before 2026-07-31

Anything scored at beta 1 is comparable to other beta-1 results and **not** to results under
this rule. Re-derive rather than mix. The first re-derivation is `intraday_curve34`; see
`docs/BACKTEST_LEDGER.md`.
