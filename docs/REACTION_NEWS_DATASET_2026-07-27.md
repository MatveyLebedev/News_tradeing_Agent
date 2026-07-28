# Market-reaction articles in `news` — census and dataset feasibility (2026-07-27)

Question asked: **are there articles in the DB that describe the market's price reaction to
an event, and can they be turned into a dataset `inputs -> market reaction`?**

Short answer: **yes for the label, mostly yes for the input, no for anything tradable.**
~2,250 articles are price-reaction reports with a stated cause; their direction can be
recovered from our own price tables at 86-95% agreement once the label window is matched to
the publication bucket. But the median such article is published *after 99% of the day's
move has already printed*, so the dataset is a **supervised map "event -> reaction"**, never
a signal. Reproduce everything below with `python -m scripts.scan_reaction_news --validate`.

---

## 1. What the corpus actually is

| | |
|---|---|
| rows in `news` | 35,618 |
| dense period | **2026-02-23 → 2026-07-26** (~5 months); 13 stragglers dated 2024-12 → 2026-01 |
| text available | `title` + `summary` (~200 chars). `full_text` is populated but empty in practice — **avg length 24 chars** |
| embeddings | 32,306 / 35,618 rows |
| price side | `stock_prices_daily` 132 tickers × 527 sessions (2024-06-17 →); minute bars in `historical_data_<tkr>` for 120 tickers **from 2026-02-23** — i.e. intraday coverage starts exactly where the news corpus does |
| benchmarks | `SPY` + 11 sector ETFs in the daily table; `macro_series` has `wti`, `brent`, `vix`, `dgs10`, `hy_oas` |
| `stock_prices` (intraday, single table) | **empty — 0 rows.** All minute data lives in the per-ticker tables |

## 2. Finding them

A reaction headline has a fixed shape:

```
<asset subject> <move verb> [<magnitude>] <connector> <cause clause>
"Fortinet Jumps on Outlook Hike, Billings Growth Spurt"
"Korean Stocks Slump 6% as Chipmakers Hit by Renewed Selloff"
"Alphabet shares fall on report its most powerful AI model Gemini 3.5 Pro is delayed"
```

`scripts/scan_reaction_news.py` matches that shape. Two details do most of the work:

* **the mover is whatever precedes the first move verb**, not any entity in the line.
  Without this rule *"Nebius stock surges as Nvidia discloses 9.3% stake"* is filed under
  NVDA, and *"Nvidia's Huang Spurs Asia Tech Rally"* becomes an NVDA reaction. Anchoring on
  the pre-verb span moved single-name hits from 166 to 134 — the 32 removed were all
  mislabeled movers.
* **the connector must follow the verb.** `"... as oil jumps"` is a cause clause, not a
  reaction subject.

### Census

| tier | n | share of corpus |
|---|---:|---:|
| headline contains a price-move verb (idioms like "drops out", "falls short" removed) | 5,808 | 16.3% |
| **+ asset subject + causal connector** → **STRICT** | **2,247** | **6.3%** |
| + explicit magnitude (`%` / bps) in the headline | 206 | 0.6% |

STRICT by what moves:

| mover | n |
|---|---:|
| market_wide (indices, futures, yields, dollar) | 936 |
| commodity | 622 |
| other_asset (foreign/unlisted single names, bonds, regional baskets) | 501 |
| single_name mapped to a ticker we price | 134 |
| fx / rates | 40 |
| crypto | 14 |

Roughly flat over time — 446/538/486/424/316 per month Mar→Jul — so it is a stable ~15/day
production rate, not a burst. `bloomberg-markets` supplies 56% of them.

## 3. Can the reaction be recovered from our prices?

Yes, and the label window has to be chosen by **publication bucket** (ET). For the 105
mapped single-name reaction headlines, direction agreement against each candidate window:

| publication bucket | n | gap `prevC→open` | day `prevC→close` | `open→close` | next gap | next day |
|---|---:|---:|---:|---:|---:|---:|
| premarket (<09:30) | 31 | **94%** | 81% | 58% | 65% | 61% |
| session (09:30–16:00) | 44 | 91% | **95%** | 73% | 50% | 55% |
| after-hours (>16:00) | 28 | 71% | 89% | 75% | 79% | **86%** |

That is the labeling rule, and it is not optional: a premarket article graded on
`open→close` agrees only 58% of the time — coin-flip — while the same article graded on the
gap agrees 94%. Nearly every "wrong" label in a naive same-day join is a bucket mismatch
(`"Oracle stock rises in premarket..."` closing the day −1.28%,
`"ServiceNow stock sinks 14%"` filed 16:29 against a +2.93% session that already closed).

Effect sizes are large — these articles are written *because* something moved: median
|realized move| **4.73%**, 96/127 ≥ 2%, 57/127 ≥ 5%. Labels are not noise-dominated.

Benchmark classes label too, at lower fidelity because the headline's index and ours differ:

* US market-wide vs `SPY` close-to-close: **60/85 = 71%**
* oil vs `macro_series.wti`: **162/229 = 71%**

The 29% gap is mostly cross-market ("Asian stocks", "FTSE") and intraday-vs-close timing,
not detector error.

## 4. The finding that decides what this dataset is *for*

Using minute bars, decomposing each single-name reaction into the move already printed at
the moment of publication vs. what was left afterwards:

* share of the day's move already printed **at publication time: median 99%** (p25 78%, p75 118%)
* signed return remaining after publication: median **+0.05%** (p10 −2.40%, p90 +2.89%)
* the remaining move continues in the headline's direction **54% of the time** (n=126)

54% on n=126 is indistinguishable from a coin flip. IBM's −25% day is typical: the article
lands at 07:23 with −22.7% already in the tape and −3.2% left.

So: **the reaction is not predictable from the article that reports it, because the article
is downstream of the reaction.** Any model trained on these and evaluated on forward returns
will look brilliant in-sample and produce nothing live. Under `docs/PIT_RULES.md` §0 the
article's own reaction clause is future data relative to any decision that could have traded
it — the reaction is a **label only**.

## 5. Noise classes to strip (measured, not hypothetical)

| class | n in STRICT | example |
|---|---:|---|
| subject is a **fundamental**, not a price | 79 | "TotalEnergies Profit Jumps 68% as War Upends Energy Markets" |
| forward-looking / forecast, no realized move | ~278 (upper bound, regex is loose) | "Asian Stocks Set to Fall as Oil Surges Past $100" |
| mover outside our price universe | 394 of 503 stock-subject | Nebius, Wistron, EasyJet, SK Hynix, Reddit, Lynas |
| duplicate coverage of the same cell | 109 headlines → 90 distinct (ticker, day) cells, max 3 | three IBM articles on 2026-07-14 |
| second-order reaction (the mover is not the named company) | fixed by the pre-verb rule | "Cerebras stock gains on AMD partnership" |

## 6. What the cause side looks like

Multi-label keyword pass over the clause after the connector (STRICT set):

| cause | n | share |
|---|---:|---:|
| geopolitics / war | 885 | 39% |
| product / tech | 217 | 10% |
| M&A / capital | 215 | 10% |
| earnings / guidance | 202 | 9% |
| macro / policy | 197 | 9% |
| supply / demand | 124 | 6% |
| analyst action | 23 | 1% |
| legal / regulatory | 12 | 1% |
| uncategorised | 716 | 32% |

**39% of every reaction in this corpus is the Iran war.** The five months are one regime, and
the strongest learnable pattern in the data will be "war headline → oil up, stocks down".
That is a property of Feb–Jul 2026, not of markets — the same trap `wf_fresh30_holdout`
already documented for the direction split in `docs/BACKTEST_LEDGER.md`.

## 7. Verdict — three datasets, with honest sizes

**A. Event → reaction sign/magnitude (single names).** ~105–130 examples after dedup and
noise stripping, ~90 distinct (ticker, day) cells. Label = the bucket-matched window from §3.
Input = the cause clause **with the reaction clause stripped**, since the headline states the
answer. Verdict: **buildable and clean, but too small to train on.** Its real use is as an
eval set / few-shot analog library — "this event type historically produced a move of this
size and sign" — which is exactly the shape `shift_library` already stores.

**B. Event → market/commodity reaction.** ~1,550 examples (market_wide + commodity), labels
off SPY/WTI at 71% fidelity. Bigger, but the label is coarser and the war concentration is
worse. Useful for a macro-event taxonomy, not for names.

**C. Reaction-attribution pairs (no prices at all).** All 2,247 STRICT rows as
`(cause clause, direction, magnitude-if-stated)` — a distant-supervision corpus of
"what the market rewarded/punished, as a journalist described it". No price join needed, so
no coverage loss, and it is the only variant where the corpus size is the full 2,247. Good
for training/evaluating the *map* stage (does the model assign the same sign to an event
that the market did), which is the node `evals/build_datasets.py::build_bucket_map` currently
labels from graph sentiment instead.

**The binding constraints, in order:**

1. **5 months, one regime.** No walk-forward split of any value exists inside Feb–Jul 2026.
2. **394 of 503 single-name movers are not in our 132-ticker price universe.** Widening the
   price universe is the single highest-leverage fix — it roughly 4×'s variant A.
3. **`full_text` is empty.** The cause clause is limited to the headline plus a ~200-char
   summary, so cause extraction has one sentence to work with.
4. **The input leaks the label.** Any build must strip the reaction clause from the text, and
   the stripping must be verified — otherwise the "model" is reading the answer.

**What I would not do:** grade any of these against forward returns, or let a reaction
article into a corpus a decision reads at `as_of`. §4 says the tradable content is already
gone by the time the article exists.
