# Fundamentals — what we use, why, and how to tune it

Working doc for the expert tuning session. Grounded in the actual code:
`tools/fundamentals_tool.py`, `../bloomberg_news_fetcher/edgar_fetcher.py`,
`../bloomberg_news_fetcher/fundamentals_fetcher.py`, `agent/nodes.py`.

**Key context:** this agent **trades news shifts**, not fundamentals. Fundamentals are
*not* a standalone trigger — they feed an LLM verdict (`confirms` / `contradicts` /
`ignores` the news shift), which then drives a conviction matrix
(`agent/nodes.py:357`, `agent/nodes.py:621`). "Tuning" therefore means: *which* signals
get surfaced, at *what thresholds*, and how they map to conviction — not a numeric weight
vector. The `Proposed (expert)` column is for capturing decisions live.

## Table A — Fundamentals surfaced to the agent's verdict

Fields actually placed in the LLM prompt at `agent/nodes.py:347-364`.

| # | Fundamental | Exact definition (source) | Why it's used — business value | Tuning lever / question | Proposed (expert) |
|---|---|---|---|---|---|
| 1 | **revenue_yoy** | `revenue(latest Q) / revenue(same Q ~1yr ago) − 1`; EDGAR as-filed quarterly, YoY-matched (`fundamentals_tool.py:258`) | Core "is the business growing" check; YoY removes seasonality | What YoY % = bullish? Absolute, or relative to sector / own history? | |
| 2 | **revenue_qoq** | `revenue(latest Q) / revenue(prior Q) − 1` | Acceleration / momentum vs last quarter | QoQ is seasonal-noisy — keep, drop, or only as accel vs YoY? | |
| 3 | **gross_margin** (6-Q series) | `gross_profit / revenue` per quarter (`fundamentals_tool.py:96`) | Pricing power & unit economics; trend > level | Judge level or slope? Is 6-quarter window right? | |
| 4 | **operating_margin** (series) | `operating_income / revenue` | Operating leverage / cost discipline | Which margin dominates by sector? | |
| 5 | **net_margin** (series) | `net_income / revenue` | Bottom-line efficiency; the only meaningful margin for banks (no gross/op tags) | Weight vs gross margin? | |
| 6 | **trailingPE** (PIT) | `marketCap / TTM net income`, reconstructed EDGAR×price, split-immune (`fundamentals_tool.py:202`) | Is the growth already priced in | Absolute PE band vs sector / historical percentile? | |
| 7 | **priceToBook** (PIT) | `marketCap / stockholders_equity` | Asset-backed valuation — key for financials/banks | Relevance gating by sector? | |
| 8 | **priceToSales** (PIT) | `marketCap / TTM revenue` | Valuation for low/no-earnings growth names | When to prefer P/S over P/E? | |
| 9 | **forwardPE, enterpriseToEbitda, ROE, revenueGrowth, earningsGrowth** | yfinance `info` snapshot, supplemental (`fundamentals_tool.py:275`) | Forward valuation + quality context | Trust forward analyst estimates? Snapshot is forward-only (asymmetry vs PIT) | |
| 10 | **marketCap** | `close × implied diluted shares` | Size / liquidity / risk gating | Min-cap floor before trading? | |
| 11 | **fcf** (freeCashflow) | yfinance `info` (`fundamentals_tool.py:280`) | Cash generation — harder to manipulate than earnings | FCF-yield threshold? Move to PIT FCF (OCF − capex from EDGAR)? | |
| 12 | **debt / net_debt** | `totalDebt − totalCash` (yfinance) (`fundamentals_tool.py:281`) | Balance-sheet risk; leverage amplifies drawdowns | Net-debt/EBITDA limit — hard gate or soft score? | |
| 13 | **expectation_gap** ⭐ | `net = upLast30days − downLast30days` EPS estimate revisions (`fundamentals_tool.py:103`) | Labeled in code as "the alpha signal" — analyst revision momentum predicts post-drift | **Biggest knob:** window (30d), up/down threshold, weight in verdict | |
| 14 | **sector / industry** | yfinance `info` | Peer normalization; decides which metrics apply (e.g. banks) | Make all thresholds sector-relative? | |

## Table B — Fetched & stored, but NOT yet surfaced to the verdict

Pulled by the fetchers but never reaching the decision prompt — candidates for "what to add."

| Source | Fields collected but unused | Potential business value if wired in |
|---|---|---|
| EDGAR income (`edgar_fetcher.py:72`) | `rd_expense`, `sga_expense`, `eps_basic` | R&D intensity, cost-structure trend |
| EDGAR balance (`edgar_fetcher.py:77`) | `total_assets`, `current_assets/liabilities`, `long_/short_term_debt`, `inventory` | PIT current ratio & leverage (today debt is forward-only yfinance) |
| EDGAR cash flow (`edgar_fetcher.py:88`) | `operating_cash_flow`, `capex`, `depreciation_amortization`, `dividends_paid`, `stock_buyback` | True PIT FCF; buyback/dividend capital-return signal |
| yfinance (`fundamentals_fetcher.py:38`) | `returnOnAssets`, `ebitdaMargins`, `enterpriseToRevenue`, `beta`, `targetMeanPrice` & analyst targets, `recommendationMean`, 52-wk high/low, `earnings_dates`, forward `analyst_estimates` | Analyst price-target gap, earnings-date proximity, volatility (beta) for sizing |

## The 3 biggest tuning levers

1. **expectation_gap config** — the one signal the code explicitly calls *alpha*, yet the
   30-day window and up/down thresholds are unvalidated. Highest expected payoff.
2. **The conviction matrix** (`agent/nodes.py:621-627`) — currently qualitative LLM
   judgment ("shift POSITIVE + fundamentals improving → high-conviction LONG"). Should the
   mappings and the `confirms/contradicts/ignores` boundaries become explicit thresholds?
3. **Valuation asymmetry** — PE/PB/PS exist in two forms (PIT EDGAR vs forward-only
   yfinance snapshot). Forward-only fields aren't available historically, so backtest ≠
   live. Decide which to trust.
