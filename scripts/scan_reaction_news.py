"""Census + label-feasibility check for 'market reaction' articles in the news table.

A market-reaction article reports a PRICE MOVE and attributes it to a cause:

    <asset subject> <move verb> [<magnitude>] <connector> <cause clause>
    "Fortinet Jumps on Outlook Hike"   "Asian Stocks Set to Fall as Oil Surges"

The scan answers two questions the dataset design depends on:
  1. how many such articles exist, and what moves in them (single name / index / commodity);
  2. can the reaction be recovered from the price tables we hold — i.e. does the
     direction stated in the headline agree with a realized return, and over which window.

Usage:
    python -m scripts.scan_reaction_news              # census only (fast)
    python -m scripts.scan_reaction_news --validate   # + join to prices (slower)
"""
import argparse
import re
import sys
from collections import Counter

from core.db import fetch_all
from longterm_agent.company_names import COMPANY_NAMES

# --- the move lexicon ---------------------------------------------------------
UP = (r"(?:jump|jumps|jumped|surge|surges|surged|soar|soars|soared|rall(?:y|ies|ied)|"
      r"climb|climbs|climbed|gain|gains|gained|rise|rises|rose|advance|advances|advanced|"
      r"pop|pops|popped|spike|spikes|spiked|rebound|rebounds|rebounded|leap|leaps|"
      r"higher|extend gains)")
DOWN = (r"(?:fall|falls|fell|drop|drops|dropped|slide|slides|slid|slump|slumps|slumped|"
        r"plunge|plunges|plunged|tumble|tumbles|tumbled|sink|sinks|sank|decline|declines|"
        r"declined|retreat|retreats|slip|slips|slipped|dive|dives|dived|sag|sags|tank|tanks|"
        r"sell(?:s)?[ -]off|selloff|sell-off|rout|lower|erase|erases|pare|pares|weaken|weakens)")
MOVE = rf"(?:{UP}|{DOWN})"
RX_MOVE = re.compile(rf"\b{MOVE}\b", re.I)

# idioms that use a move verb without describing a price move
FALSE_FRIENDS = re.compile(
    r"\b(?:drops? out|falls? short|rise of|fall of|rise and fall|gains? ground|"
    r"gain traction|rise to power|falling behind)\b", re.I)

# the cause connector has to FOLLOW the verb — "Jumps on Outlook Hike", not "As oil jumps"
RX_CAUSE = re.compile(
    rf"\b{MOVE}\b[^,;]{{0,60}}?\b(as|after|on|amid|following|over|despite|with|because|"
    r"thanks to|on the back of|spurred by|driven by|fueled by|led by|hit by)\b", re.I)
RX_MAG = re.compile(r"\b\d{1,3}(?:\.\d+)?\s?(?:%|percent|pct)|\b\d{1,4}\s?(?:basis points|bps)", re.I)

ASSET = re.compile(
    r"\b(stock|stocks|shares?|equit(?:y|ies)|index|indexes|indices|futures?|"
    r"s&p|nasdaq|dow|russell|nikkei|hang seng|ftse|dax|cac|kospi|sensex|"
    r"yields?|bonds?|treasur(?:y|ies)|bunds?|gilts?|credit spreads?|"
    r"dollar|euro|yen|yuan|peso|lira|rupee|won|sterling|pound|franc|real|currenc(?:y|ies)|fx|"
    r"oil|crude|brent|wti|gas|gasoline|diesel|jet fuel|lng|"
    r"gold|silver|copper|aluminium|aluminum|nickel|zinc|iron ore|platinum|palladium|lithium|"
    r"uranium|coal|wheat|corn|soybeans?|coffee|cocoa|sugar|cotton|"
    r"bitcoin|ether|ethereum|crypto|token|"
    r"market|markets|rally|selloff|sell-off|rout|swap rates?|price|prices)\b", re.I)
MARKET_WIDE = re.compile(
    r"\b(s&p|nasdaq|dow|stocks|equit(?:y|ies)|markets?|futures?|treasur(?:y|ies)|yields?|"
    r"dollar|bonds?|wall street)\b", re.I)
COMMODITY = re.compile(
    r"\b(oil|crude|brent|wti|gas|gold|silver|copper|aluminium|aluminum|nickel|zinc|iron ore|"
    r"platinum|palladium|lithium|uranium|coal|wheat|corn|soybeans?|coffee|cocoa|sugar|cotton|"
    r"power price|jet fuel|diesel|gasoline|lng)\b", re.I)
CRYPTO = re.compile(r"\b(bitcoin|ether|ethereum|crypto|solana|xrp|dogecoin)\b", re.I)
FX_RATES = re.compile(r"\b(dollar|euro|yen|yuan|peso|lira|rupee|won|sterling|franc|yields?|"
                      r"bonds?|treasur(?:y|ies)|swap rates?)\b", re.I)
STOCK_SUBJ = re.compile(r"\b(shares?|stock)\b", re.I)
# a fundamental moving is not a price moving
FUNDAMENTAL = re.compile(
    r"\b(profit|revenue|sales|earnings|margin|income|pay|output|production|imports?|exports?|"
    r"deliveries|orders?|backlog|capex|spending|demand|shipments?|subscribers?|users?)\b", re.I)

NAME2TICK = {}
for _tk, _names in COMPANY_NAMES.items():
    for _n in _names:
        NAME2TICK.setdefault(_n.lower(), _tk)
RX_NAMES = re.compile(r"\b(" + "|".join(sorted((re.escape(n) for n in NAME2TICK),
                                               key=len, reverse=True)) + r")\b", re.I)


def classify(title: str):
    """None if the headline is not a price-move report, else its structure."""
    t = title or ""
    if FALSE_FRIENDS.search(t):
        return None
    m = RX_MOVE.search(t)
    if not m:
        return None
    pre = t[:m.start()]                       # the mover is whatever precedes the verb
    word = m.group(0).lower()
    tickers = sorted({NAME2TICK[x.lower()] for x in RX_NAMES.findall(pre)})
    if tickers and not MARKET_WIDE.search(pre):
        cls = "single_name"
    elif MARKET_WIDE.search(t):
        cls = "market_wide"
    elif COMMODITY.search(t):
        cls = "commodity"
    elif CRYPTO.search(t):
        cls = "crypto"
    elif FX_RATES.search(t):
        cls = "fx_rates"
    elif ASSET.search(t):
        cls = "other_asset"
    else:
        cls = "no_asset_subject"
    return dict(
        cls=cls,
        direction="up" if re.fullmatch(UP, word, re.I) else "down",
        cause=bool(RX_CAUSE.search(t)),
        asset=bool(ASSET.search(t)),
        magnitude=bool(RX_MAG.search(t)),
        stock_subject=bool(STOCK_SUBJ.search(pre)),
        fundamental_subject=bool(FUNDAMENTAL.search(pre)),
        tickers=tickers,
    )


def load_hits():
    rows = fetch_all("SELECT id, published, source, title FROM news ORDER BY published")
    hits = [(nid, pub, src, title, c)
            for nid, pub, src, title in rows if (c := classify(title))]
    return len(rows), hits


def census(total, hits):
    strict = [h for h in hits if h[4]["asset"] and h[4]["cause"]]
    print(f"news rows                       {total}")
    print(f"headlines with a move verb      {len(hits):6}  ({100*len(hits)/total:.1f}%)")
    print(f"  + asset subject + cause       {len(strict):6}  ({100*len(strict)/total:.1f}%)   <- STRICT")
    print(f"  + explicit magnitude          {sum(h[4]['magnitude'] for h in strict):6}")
    print("\nSTRICT by mover class")
    for k, v in Counter(h[4]["cls"] for h in strict).most_common():
        print(f"  {k:18} {v:6}")
    print("\nSTRICT by source")
    for k, v in Counter(h[2] for h in strict).most_common(8):
        print(f"  {k:22} {v:6}")
    print("\nSTRICT by month")
    for k, v in sorted(Counter(h[1].strftime("%Y-%m") for h in strict).items()):
        print(f"  {k}  {v}")
    stockish = [h for h in strict if h[4]["stock_subject"]]
    mapped = [h for h in stockish if h[4]["tickers"]]
    print(f"\nmover is a stock/shares         {len(stockish):6}")
    print(f"  mapped to a priced ticker     {len(mapped):6}")
    print(f"  outside our price universe    {len(stockish)-len(mapped):6}")
    print(f"subject is a fundamental, not a price: "
          f"{sum(h[4]['fundamental_subject'] for h in strict)}")
    return strict, mapped


def validate(mapped):
    """Does the headline direction match a realized return, and over which window?"""
    single = [h for h in mapped if len(h[4]["tickers"]) == 1]
    px = {}
    for tk in sorted({h[4]["tickers"][0] for h in single}):
        rows = fetch_all("SELECT date, open, close FROM stock_prices_daily "
                         "WHERE ticker=%s ORDER BY date", (tk,))
        px[tk] = ([r[0] for r in rows], {r[0]: (float(r[1]), float(r[2])) for r in rows})

    def bucket(ts):
        t = ts.time()
        if t.hour < 9 or (t.hour == 9 and t.minute < 30):
            return "premarket"
        return "session" if t.hour < 16 else "afterhours"

    res = {b: Counter() for b in ("premarket", "session", "afterhours")}
    for _nid, pub, _src, title, c in single:
        tk = c["tickers"][0]
        dates, m = px[tk]
        d = pub.date()
        if d not in m:
            continue                                   # weekend / holiday publication
        i = dates.index(d)
        if i == 0 or i + 1 >= len(dates):
            continue
        prev_c = m[dates[i - 1]][1]
        o, cl = m[d]
        nxt_o, nxt_c = m[dates[i + 1]]
        up = c["direction"] == "up"
        b = bucket(pub)
        for k, v in {"gap prevC->open": (o - prev_c) / prev_c,
                     "day prevC->close": (cl - prev_c) / prev_c,
                     "open->close": (cl - o) / o,
                     "next gap close->nextO": (nxt_o - cl) / cl,
                     "next day close->nextC": (nxt_c - cl) / cl}.items():
            res[b][k + "|n"] += 1
            res[b][k + "|ok"] += (v > 0) == up
    print("\ndirection agreement per publication bucket x label window")
    for b in ("premarket", "session", "afterhours"):
        n = max((v for k, v in res[b].items() if k.endswith("|n")), default=0)
        print(f"\n  [{b}] n={n}")
        for k in ("gap prevC->open", "day prevC->close", "open->close",
                  "next gap close->nextO", "next day close->nextC"):
            nn, ok = res[b][k + "|n"], res[b][k + "|ok"]
            if nn:
                print(f"    {k:24} {ok:3}/{nn:3} = {100*ok/nn:3.0f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true", help="join to price tables")
    args = ap.parse_args()
    total, hits = load_hits()
    _strict, mapped = census(total, hits)
    if args.validate:
        validate(mapped)


if __name__ == "__main__":
    sys.exit(main())
