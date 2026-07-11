# Missing Indicators — Data Gathering Checklist

Indicators required for the narrative engine that are **not currently surfaced to the agent's verdict**.
Baseline = Table A of `FUNDAMENTALS_TUNING.md` (revenue YoY/QoQ, margins, PIT PE/PB/PS, forward PE, EV/EBITDA, ROE, market cap, FCF, net debt, expectation_gap, sector).

Status legend: **NEW** = not fetched anywhere today · **STORED** = already fetched by your pipeline but never wired into the verdict (Table B).

---

## Layer 1 — Macro Surprise Engine

For every series, store: `actual`, `consensus`, `prior`, `release_datetime`. Core computed value: **surprise z-score** = (actual − consensus) / stdev of historical surprises for that series.

### Inflation cluster
| # | Indicator | Frequency | Source (actuals) | Status |
|---|---|---|---|---|
| 1 | Headline CPI, MoM & YoY | Monthly | BLS / FRED `CPIAUCSL` | NEW |
| 2 | Core CPI (ex food & energy), MoM & YoY | Monthly | BLS / FRED `CPILFESL` | NEW |
| 3 | Core PCE Price Index, MoM & YoY | Monthly | BEA / FRED `PCEPILFE` | NEW |
| 4 | PPI, MoM | Monthly | BLS / FRED `PPIACO` | NEW |
| 5 | UMich 1-year consumer inflation expectations | Monthly | FRED `MICH` | NEW |

### Labor cluster
| # | Indicator | Frequency | Source (actuals) | Status |
|---|---|---|---|---|
| 6 | Initial jobless claims | Weekly | DOL / FRED `ICSA` | NEW |
| 7 | ADP non-farm employment | Monthly | ADP / FRED `ADPWNUSNERSA` | NEW |
| 8 | Non-farm payrolls (headline & private) | Monthly | BLS / FRED `PAYEMS` | NEW |
| 9 | Unemployment rate | Monthly | BLS / FRED `UNRATE` | NEW |
| 10 | Challenger job cuts | Monthly | Challenger, Gray & Christmas releases | NEW |
| 11 | Labor quality adjustments: government-hiring share of payrolls, part-time-for-economic-reasons | Monthly | BLS CES/CPS detail tables | NEW |

### Growth / activity cluster
| # | Indicator | Frequency | Source (actuals) | Status |
|---|---|---|---|---|
| 12 | ISM Manufacturing PMI | Monthly | ISM | NEW |
| 13 | ISM Services (Non-Mfg) PMI | Monthly | ISM | NEW |
| 14 | S&P Global Manufacturing & Services PMIs | Monthly (flash + final) | S&P Global | NEW |
| 15 | Chicago PMI | Monthly | MNI | NEW |
| 16 | Consumer Confidence Index | Monthly | Conference Board | NEW |
| 17 | Retail sales, MoM | Monthly | Census / FRED `RSAFS` | NEW |
| 18 | GDP growth (QoQ annualized, advance/second/final) | Quarterly | BEA / FRED `GDPC1` | NEW |

### Computed composites (no external data — derived from #1–18)
| # | Indicator | Definition |
|---|---|---|
| 19 | `inflation_pulse` | Rolling mean of surprise z-scores in the inflation cluster |
| 20 | `labor_pulse` | Rolling mean of surprise z-scores in the labor cluster |
| 21 | `growth_pulse` | Rolling mean of surprise z-scores in the growth cluster |

---

## Layer 2 — Monetary Policy & Rates

| # | Indicator | Frequency | Source | Status |
|---|---|---|---|---|
| 22 | Fed funds target range + decision log (hike/hold/cut) | Per FOMC | Fed / FRED `DFEDTARU` | NEW |
| 23 | FOMC dot-plot median (current & next year-end) + revision vs. prior SEP | Quarterly | Fed SEP releases | NEW |
| 24 | Fed-funds futures implied probabilities (next meeting + year-end) and **week-over-week delta** | Daily | CME FedWatch | NEW |
| 25 | Fed balance sheet total → QE/QT direction flag | Weekly | FRED `WALCL` | NEW |
| 26 | Treasury General Account (TGA) balance | Weekly | FRED `WTREGEN` | NEW |
| 27 | Overnight reverse repo (RRP) balance | Daily | FRED `RRPONTSYD` | NEW |
| 28 | 10Y Treasury yield: level + N-session change (flag Δ ≥ 40 bp / 15 sessions as "hidden hike/cut" shock) | Daily | FRED `DGS10` | NEW |
| 29 | 10Y auction quality: high yield vs. when-issued (tail), bid-to-cover | Per auction | TreasuryDirect auction results | NEW |
| 30 | Equity risk premium: S&P 500 forward earnings yield − 10Y; equilibrium P/E band = 1 / (10Y + 3–4%) → `overvaluation_gap` | Daily | Computed (#28 + index forward EPS) | NEW |

---

## Layer 3 — Credit, Volatility, Positioning (regime detectors)

| # | Indicator | Frequency | Source | Status |
|---|---|---|---|---|
| 31 | High-yield credit spread (BofA HY OAS): level, 5-yr percentile, 20-day rate of change | Daily | FRED `BAMLH0A0HYM2` | NEW |
| 32 | VIX: level (>30 capitulation flag) | Daily | CBOE / yfinance `^VIX` | NEW |
| 33 | VIX term structure (spot vs. 3-month) inversion flag | Daily | CBOE `^VIX` vs `^VIX3M` | NEW |
| 34 | FINRA margin debt: consecutive-month contraction counter | Monthly | FINRA margin statistics file | NEW |
| 35 | Equity put/call ratio | Daily | CBOE | NEW |
| 36 | Short-dated (0–1 DTE) options volume share — systemic leverage proxy | Daily | CBOE volume stats | NEW |

---

## Layer 4 — Commodities & Geopolitics

| # | Indicator | Frequency | Source | Status |
|---|---|---|---|---|
| 37 | Brent & WTI: level  (<$80 deflationary / >$100 hawkish) | Daily | yfinance `BZ=F`, `CL=F` | NEW |
| 38 | Oil→CPI pass-through term: sustained +$10 move → +0.2–0.3 pp on forward YoY CPI estimate | Computed | Derived from #37 | NEW |
| 39 | Freight / chokepoint proxy: container freight index or Baltic Dry | Weekly | Freightos FBX / Baltic Exchange | NEW |

---

## Layer 5 — Micro Extensions (per ticker, proxy list)

| # | Indicator | Frequency | Source | Status |
|---|---|---|---|---|
| 40 | Revenue & EPS **guidance vs. street consensus** (next Q and FY) | Per earnings | Press releases / earnings calls (LLM extraction) + consensus feed | NEW |
| 41 | **Key-segment revenue vs. segment-level consensus** (e.g., AI semiconductor segment) | Per earnings | Press releases / calls (LLM extraction) | NEW |
| 42 | Capex actual vs. guided capex; overrun in $ and % | Per earnings | EDGAR cash flow + guidance extraction | STORED (capex) + NEW (guided) |
| 43 | True PIT FCF = operating cash flow − capex | Quarterly | EDGAR (already fetched) | STORED |
| 44 | Capex / OCF ratio (capex intensity) | Quarterly | EDGAR (already fetched) | STORED |
| 45 | Capital return: buybacks + dividends paid, and vs. FCF | Quarterly | EDGAR (already fetched) | STORED |
| 46 | PIT leverage: total/long-term/short-term debt, current ratio from balance sheet | Quarterly | EDGAR (already fetched) | STORED |
| 47 | R&D intensity (rd_expense / revenue) and SG&A trend | Quarterly | EDGAR (already fetched) | STORED |
| 48 | Dilution risk: announced equity issuance / market cap | Event-driven | Filings (S-1/424B, 8-K) | NEW |
| 49 | Relative multiple gap: forward P/E vs. peer-group median & percentile (e.g., semis vs. hyperscalers) | Daily | Computed from existing forwardPE + peer grouping | NEW (computation) |
| 50 | EPS trajectory sustainability: sequential EPS path slope + quarters of that slope implied by current price | Quarterly | Computed from EDGAR `eps_basic` (already fetched) | STORED (data) + NEW (computation) |
| 51 | Contract/backlog duration: enterprise partnership horizon (e.g., custom-chip deals through 2029) | Event-driven | Calls & filings (LLM extraction) | NEW |
| 52 | Earnings-date proximity flag | Daily | yfinance `earnings_dates` (already fetched) | STORED |
| 53 | Analyst price-target gap (`targetMeanPrice` / price − 1) & `recommendationMean` | Daily | yfinance (already fetched) | STORED |
| 54 | Beta (for sizing) & 52-week high/low distance | Daily | yfinance (already fetched) | STORED |

---

## Layer 6 — IPO Pipeline & Liquidity Drains

| # | Indicator | Frequency | Source | Status |
|---|---|---|---|---|
| 55 | Marquee IPO pipeline: filings, target valuation, timeline changes (delays = warning) | Event-driven | S-1 filings, news layer | NEW |
| 56 | Liquidity leakage: announced raise size vs. sector ETF AUM (forced rebalancing pressure) | Event-driven | Computed from #55 + ETF AUM | NEW |

---

## Composite Regime States (computed from the above, no new data)

| # | State | Inputs |
|---|---|---|
| 57 | Liquidity quadrant A/B/C/D (QE/QT × rate level) | #22, #25 |
| 58 | 5-Box bottom checklist (VIX>30 · hiking cycle over · margin-debt contraction · structural theme present · leaders beating during crisis) | #32, #23/#24, #34, news layer, #40 |
| 59 | Tail-risk filters (credit freeze · inflation deadlock · accounting-trust breakdown) | #31, #19 vs #22, news layer |

---

## Notes on data gathering

- **Consensus/forecast values are the bottleneck** for Layer 1 and #40–41. Actuals are free (FRED/BLS/BEA); consensus requires scraping (investing.com, TradingEconomics) or a paid feed. Decide this first — without consensus there is no surprise z-score.
- Store **everything point-in-time** (`asof` timestamp, first-release value, not revised value) or the narrative agent can never be backtested.
- Items marked **STORED** need zero data work — only wiring already-fetched fields into the state schema. Do these first.
