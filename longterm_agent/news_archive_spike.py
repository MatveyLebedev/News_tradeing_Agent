"""
News-archive source-evaluation spike (Backtest Plan B2).

The live corpus is Bloomberg/CNBC/MarketWatch *RSS*, which is forward-only — so usable history
starts ~2026-02 and historical backtests are capped at ~8 cohorts. Before committing to a heavy
backfill (load → re-embed → rebuild Neo4j AFFECTS), this spike measures whether **GDELT DOC 2.0**
(free, no key, indexes online news back years) can supply pre-feed articles with acceptable
*source parity* to the live feed.

It answers two questions, per ticker:
  A. PARITY  — over a window we ALSO have via RSS, how much does GDELT (filtered to our domains)
     overlap what we already hold? (does GDELT "see" the same Bloomberg/CNBC/MarketWatch stream?)
  B. DEPTH   — over a pre-feed window, how many on-topic, same-domain articles can GDELT supply?
     (how much historical depth a backfill would actually add, where our table is ~empty.)

This is an evaluation tool, not an importer. It only READS GDELT (public data) and our `news`
table. Run:  python -m longterm_agent.news_archive_spike
"""
import sys
import time
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from longterm_agent.config import LongTermAgentConfig

GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
# GDELT 429s the default python-requests UA outright; a browser UA gets 200 (same trick the live
# RSS fetcher uses). Without this every call silently returns empty.
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"}
# Domains that make up the live feed (see news.source breakdown). GDELT is web-wide; filtering to
# these approximates the live source mix — the whole point of the parity check.
DOMAINS = ["bloomberg.com", "cnbc.com", "marketwatch.com"]
_DOMAIN_Q = "(" + " OR ".join(f"domain:{d}" for d in DOMAINS) + ")"

# Primary query phrase per spike ticker (kept curated to avoid noisy GDELT matches).
SPIKE = {
    "NVDA": "Nvidia",
    "AAPL": "Apple",
    "JPM": "\"JPMorgan\"",
    "TSLA": "Tesla",
}

# Window A: we HAVE RSS here (parity). Window B: pre-feed gap (depth).
WIN_A = ("20260401000000", "20260430235959")
WIN_B = ("20250901000000", "20251231235959")


def _norm_url(u: str) -> str:
    """Normalize for overlap matching: drop scheme, ':443', 'www.', trailing slash, lowercase."""
    if not u:
        return ""
    u = u.split("://", 1)[-1].replace(":443", "").rstrip("/").lower()
    return u[4:] if u.startswith("www.") else u


_last_call = [0.0]  # module-global pacer: GDELT limits per-IP, so space EVERY call ≥7s apart


def _pace(min_gap=7.0):
    dt = time.time() - _last_call[0]
    if dt < min_gap:
        time.sleep(min_gap - dt)
    _last_call[0] = time.time()


def gdelt_articles(phrase: str, start: str, end: str, maxrecords: int = 250, domains_q: str = None):
    """Return GDELT artlist (list of {url,title,seendate,domain,...}) or [] on failure. GDELT
    rate-limits aggressively per-IP and replies with a plain-text 429 (not JSON); we pace every
    call and retry with backoff so a throttle doesn't masquerade as 'no articles'."""
    q = f"{phrase} {domains_q or _DOMAIN_Q}"
    url = (f"{GDELT}?query={quote(q)}&mode=artlist&maxrecords={maxrecords}"
           f"&format=json&startdatetime={start}&enddatetime={end}&sort=datedesc")
    for attempt in range(4):
        _pace()
        try:
            r = requests.get(url, timeout=30, headers=HEADERS)
            if r.status_code == 429:
                time.sleep(6 * (attempt + 1))
                continue
            if r.status_code != 200 or not r.text.strip():
                return []
            return json.loads(r.text).get("articles", [])
        except Exception as e:
            print(f"    ⚠ GDELT error: {e}")
            time.sleep(4)
    return []


def our_links(cfg, ticker_terms, start: str, end: str):
    """Our news.link rows mentioning the company over [start,end] (title/summary ILIKE).
    Fresh connection per call — GDELT's multi-second pacing between calls leaves a pooled/shared
    connection idle long enough for the server to drop it."""
    s = datetime.strptime(start, "%Y%m%d%H%M%S")
    e = datetime.strptime(end, "%Y%m%d%H%M%S")
    like = " OR ".join(["title ILIKE %s OR summary ILIKE %s"] * len(ticker_terms))
    args = [s, e]
    for t in ticker_terms:
        args += [f"%{t}%", f"%{t}%"]
    conn = psycopg2.connect(**cfg.get_db_config())
    try:
        with conn.cursor() as cur:
            cur.execute(f"""SELECT link FROM news WHERE published >= %s AND published <= %s
                            AND ({like})""", args)
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def main():
    cfg = LongTermAgentConfig()

    print("News-archive spike — GDELT DOC 2.0 vs live `news`")
    print(f"domains: {DOMAINS}")
    print(f"window A (parity, we have RSS): {WIN_A[0][:8]}..{WIN_A[1][:8]}")
    print(f"window B (pre-feed depth)     : {WIN_B[0][:8]}..{WIN_B[1][:8]}\n")
    print(f"{'ticker':6s} {'GDELT_A':>8s} {'ours_A':>7s} {'overlap':>8s} {'ov%':>5s}   "
          f"{'GDELT_B':>8s} {'ours_B':>7s}   {'domains_B'}")

    for tk, phrase in SPIKE.items():
        terms = [phrase.strip('"')]
        # A — parity
        ga = gdelt_articles(phrase, *WIN_A)
        oa = our_links(cfg, terms, *WIN_A)
        ga_norm = {_norm_url(a.get("url")) for a in ga}
        oa_norm = {_norm_url(u) for u in oa}
        overlap = ga_norm & oa_norm
        ovpct = (len(overlap) / len(ga_norm) * 100) if ga_norm else 0
        # B — depth
        gb = gdelt_articles(phrase, *WIN_B)
        ob = our_links(cfg, terms, *WIN_B)
        dom_b = {}
        for a in gb:
            d = a.get("domain", "?")
            dom_b[d] = dom_b.get(d, 0) + 1
        dom_b_str = ", ".join(f"{d}:{c}" for d, c in sorted(dom_b.items(), key=lambda x: -x[1])[:3])
        print(f"{tk:6s} {len(ga):8d} {len(oa):7d} {len(overlap):8d} {ovpct:4.0f}%   "
              f"{len(gb):8d} {len(ob):7d}   {dom_b_str}")

    print("\nnote: GDELT caps artlist at 250/query, so high-volume months saturate (counts are a "
          "floor). GDELT gives url+title+date+domain — NOT summary/full_text (see findings).")


if __name__ == "__main__":
    main()
