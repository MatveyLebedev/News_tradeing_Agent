# Gold set — labeling guide (attribution + per-company sentiment)

Frozen 2026-10-22, **before any item was labeled**. Changing a definition after labeling has
started means relabeling everything labeled under the old one — note the change here with a
date instead of editing silently.

Used by: `docs/PLAN_FINETUNE_PRECHECKS_2026-10-20.md` — E2 test set T2 (all items) and the
E1 step-4 graph-only audit (the `graph_only` stratum). Sample built by
`evals/build_gold_sample.py`; files land in `longterm_agent/exports/gold/` (gitignored — they
carry news text, which must not reach the public repo).

---

## 1. What one item is

One **article × candidate company** pair. You see the article's title and summary (as the
pipeline sees them — ~200 characters, `full_text` is empty in practice) and one candidate:
ticker plus its common name(s). Answer two questions about that company and nothing else.

Candidates come from what the pipeline already links to the article, plus one sector peer
added on purpose. Some candidates are therefore wrong — rejecting them is part of the task.

**Judge from the text only.** Do not look up prices, later news or the company's website.
The labels must be constructible from what was knowable at publication.

## 2. Role — how the article relates to this company

| label | when |
|---|---|
| `subject` | The article is **about** this company: it is the main actor or the main object. "Fortinet raises outlook", "FTC sues Amazon", "Apple's iPhone sales slump in China". |
| `involved` | A **direct party** to the event, but not what the article is about: the counterparty in a deal, the partner, the named customer/supplier, the other side of a lawsuit, the investor taking a stake. "Nebius surges as **Nvidia** discloses stake" → NVDA is `involved`. |
| `context` | **Mentioned** but not a party: peer comparison, sector or index member, an example in a list, background, "like Apple and Google…". Also a company hit only through a sector/macro read-across: "Broadcom's miss drags chip stocks" → AMD (if named) is `context`. |
| `not_in_text` | The company is **not mentioned at all** — no name, ticker, brand, product or subsidiary. A sector word alone ("chipmakers", "Big Tech") does not count as a mention. |

Mentions include subsidiaries, brands and products: Waymo/YouTube → Alphabet, iPhone → Apple,
AWS → Amazon, Instagram/WhatsApp → Meta. Share classes are one company (GOOG = GOOGL).

If two companies are equally the subject ("Apple and Google sign search deal"), both are
`subject`. If the article is about a sector with no single company in focus, nobody is
`subject` — named members are `context`.

## 3. Sentiment — for THIS company

What the article implies for **this company's value or prospects**, from the company's own
point of view. Not the tone of the writing, not the market overall.

| label | when |
|---|---|
| `positive` | Good for the company: beats, raised guidance, wins a contract, approval, favourable ruling, upgrade, a stake taken in it, its stock reported rising on news. |
| `negative` | Bad for the company: misses, cuts, lawsuit or probe against it, outage, downgrade, loses a customer, its stock reported falling on news. |
| `neutral` | No clear implication: routine notices (conference appearance, scheduled dividend, filing), balanced or mixed news, pure description, or a mention with no bearing. |
| `n/a` | Only when role is `not_in_text`. |

Edge cases:
- **Same event, opposite sides.** "X wins contract from Y" — X positive; Y usually neutral.
  "X sues Y" — Y negative; X neutral unless the article frames it as a win for X.
- **Reaction headlines.** "X jumps on outlook hike" — X positive. Direction of the reported
  move counts, because the text states it.
- **"Despite / even as" headlines.** "X falls despite record profit" — label what the article
  says happened to the company: here negative. Add a note.
- **Forecasts and opinion.** "Analysts see X slowing", an op-ed against X — label the
  implication (negative), and mark `unsure` if it is pure speculation.
- **Context companies** still get a sentiment if the text implies one ("drags chip stocks" →
  negative for named chipmakers); otherwise neutral.

## 4. Uncertainty

Tick `unsure` instead of guessing hard; add a short note. Unsure items are scored separately,
not dropped. Never skip an item — every item gets a role.

## 5. Independence

Label without seeing any model's output for these articles. The sheet deliberately hides how
each candidate got there (graph or name match), the graph's own sentiment and the stratum —
that metadata sits in `gold_key.csv` and is joined only at scoring time.
