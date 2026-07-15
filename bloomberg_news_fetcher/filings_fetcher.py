"""
EDGAR filings fetcher (Modification Plan 9.1 + 9.2) — 8-K/press-releases and Form 4
insider transactions for the tracked universe.

9.1  `filings`: 8-K (+ dilution-relevant S-1/S-3/424B5) with the PRIMARY document text
     and the first EX-99* exhibit (that exhibit usually IS the press release — the only
     legally fetchable FULL TEXT we have, since news RSS bodies are blocked/paywalled).
     This is the substrate for LLM guidance-extraction (#40-41) and dilution events (#48).
9.2  `insider_trades`: Form 4 non-derivative transactions (code, shares, price, insider
     role) parsed from the ownership XML.

PIT: keyed by EDGAR `filed_date` / accession — first-release only (ON CONFLICT DO
NOTHING), append-only, fully backfillable. Reuses edgar_fetcher's SEC-compliant session
(UA + ~10 req/s cap is respected by our low volume + polite sleeps).

CLI:
    python filings_fetcher.py --backfill-days 365      # seed history for the universe
    python filings_fetcher.py                          # incremental (last 7 days)
    python filings_fetcher.py --tickers AAPL --stats
"""
import argparse
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from html.parser import HTMLParser

from psycopg2.extras import Json, execute_values

from daily_price_fetcher import get_db_connection
from edgar_fetcher import _session as edgar_session, load_cik_map
from logger import DatabaseLogger
from price_fetcher import TOP_50_TICKERS

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:0>10}.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{doc}"

FILING_FORMS = ("8-K", "8-K/A", "S-1", "S-3", "424B5")
INSIDER_FORMS = ("4", "4/A")
BODY_CAP = 150_000          # chars of stripped text per document
REQUEST_SLEEP = 0.15        # stay far under SEC's 10 req/s

logger = DatabaseLogger()

_DDL = """
CREATE TABLE IF NOT EXISTS filings (
    id          SERIAL PRIMARY KEY,
    ticker      VARCHAR(10) NOT NULL,
    cik         BIGINT,
    form        VARCHAR(12) NOT NULL,
    filed_date  DATE NOT NULL,
    accession   VARCHAR(25) NOT NULL,
    items       TEXT,
    primary_doc TEXT,
    url         TEXT,
    body_text   TEXT,
    exhibit_text TEXT,
    fetched_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE (accession)
);
CREATE INDEX IF NOT EXISTS filings_tkr_date_idx ON filings (ticker, filed_date);

CREATE TABLE IF NOT EXISTS insider_trades (
    id               SERIAL PRIMARY KEY,
    ticker           VARCHAR(10) NOT NULL,
    accession        VARCHAR(25) NOT NULL,
    seq              INTEGER NOT NULL,
    filed_date       DATE NOT NULL,
    transaction_date DATE,
    insider_name     TEXT,
    insider_title    TEXT,
    code             VARCHAR(2),
    acquired         BOOLEAN,
    shares           DOUBLE PRECISION,
    price            DOUBLE PRECISION,
    value            DOUBLE PRECISION,
    raw              JSONB,
    fetched_at       TIMESTAMP DEFAULT NOW(),
    UNIQUE (accession, seq)
);
CREATE INDEX IF NOT EXISTS insider_tkr_date_idx ON insider_trades (ticker, filed_date);
"""


def ensure_tables():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(_DDL)
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- html→text
class _TextExtract(HTMLParser):
    _SKIP = {"script", "style"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def html_to_text(html: str, cap: int = BODY_CAP) -> str:
    """Strip tags/scripts → whitespace-normalized text, capped."""
    p = _TextExtract()
    try:
        p.feed(html)
    except Exception:
        pass
    return re.sub(r"[ \t\xa0]+", " ", "\n".join(p.parts))[:cap]


# --------------------------------------------------------------------------- form 4 xml
def parse_form4(xml_text: str):
    """ownershipDocument XML → list of non-derivative transaction dicts (pure)."""
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []

    def txt(el, path):
        node = el.find(path)
        return node.text.strip() if node is not None and node.text else None

    name = txt(root, ".//reportingOwner/reportingOwnerId/rptOwnerName")
    title = txt(root, ".//reportingOwner/reportingOwnerRelationship/officerTitle")
    rel = root.find(".//reportingOwner/reportingOwnerRelationship")
    if title is None and rel is not None:
        flags = [(("isDirector", "Director"), ("isOfficer", "Officer")),
                 (("isTenPercentOwner", "10% owner"), ("isOther", "Other"))]
        for pair in flags:
            for tag, label in pair:
                v = txt(rel, tag)
                if v in ("1", "true"):
                    title = label
                    break
            if title:
                break

    out = []
    for i, tr in enumerate(root.findall(".//nonDerivativeTable/nonDerivativeTransaction")):
        code = txt(tr, ".//transactionCoding/transactionCode")
        shares = txt(tr, ".//transactionAmounts/transactionShares/value")
        price = txt(tr, ".//transactionAmounts/transactionPricePerShare/value")
        ad = txt(tr, ".//transactionAmounts/transactionAcquiredDisposedCode/value")
        tdate = txt(tr, ".//transactionDate/value")
        try:
            shares_f = float(shares) if shares else None
            price_f = float(price) if price else None
        except ValueError:
            shares_f = price_f = None
        out.append({
            "seq": i, "insider_name": name, "insider_title": title,
            "code": code, "acquired": (ad == "A") if ad in ("A", "D") else None,
            "transaction_date": tdate, "shares": shares_f, "price": price_f,
            "value": round(shares_f * price_f, 2) if shares_f and price_f else None,
        })
    return out


# --------------------------------------------------------------------------- edgar io
def _get(session, url):
    time.sleep(REQUEST_SLEEP)
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r


def recent_submissions(session, cik, since: date):
    """Yield {form, filed, accession, primary_doc, items} for filings since `since`."""
    data = _get(session, SUBMISSIONS_URL.format(cik=cik)).json()
    rec = data.get("filings", {}).get("recent", {})
    rows = zip(rec.get("form", []), rec.get("filingDate", []),
               rec.get("accessionNumber", []), rec.get("primaryDocument", []),
               rec.get("items", []) or [""] * len(rec.get("form", [])))
    for form, filed, acc, doc, items in rows:
        if filed and date.fromisoformat(filed) >= since:
            yield {"form": form, "filed": filed, "accession": acc,
                   "primary_doc": doc, "items": items}


def _filing_index(session, cik, accession):
    acc_nodash = accession.replace("-", "")
    url = ARCHIVES_URL.format(cik=cik, acc_nodash=acc_nodash, doc="index.json")
    return _get(session, url).json()


def _first_ex99(index_json):
    for item in index_json.get("directory", {}).get("item", []):
        name = (item.get("name") or "").lower()
        if "ex-99" in name or "ex99" in name:
            if name.endswith((".htm", ".html", ".txt")):
                return item.get("name")
    return None


def fetch_filing_texts(session, cik, accession, primary_doc):
    """(body_text, exhibit_text) for one filing — primary doc + first EX-99 exhibit."""
    acc_nodash = accession.replace("-", "")
    body = exhibit = None
    try:
        r = _get(session, ARCHIVES_URL.format(cik=cik, acc_nodash=acc_nodash, doc=primary_doc))
        body = html_to_text(r.text)
    except Exception as e:
        print(f"    ⚠ body fetch failed {accession}: {e}")
    try:
        ex = _first_ex99(_filing_index(session, cik, accession))
        if ex:
            r = _get(session, ARCHIVES_URL.format(cik=cik, acc_nodash=acc_nodash, doc=ex))
            exhibit = html_to_text(r.text)
    except Exception as e:
        print(f"    ⚠ exhibit fetch failed {accession}: {e}")
    return body, exhibit


# --------------------------------------------------------------------------- persist
def save_filing(cur, ticker, cik, sub, body, exhibit):
    acc_nodash = sub["accession"].replace("-", "")
    url = ARCHIVES_URL.format(cik=cik, acc_nodash=acc_nodash, doc=sub["primary_doc"])
    cur.execute("""
        INSERT INTO filings (ticker, cik, form, filed_date, accession, items,
                             primary_doc, url, body_text, exhibit_text)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (accession) DO NOTHING
    """, [ticker.upper(), cik, sub["form"], sub["filed"], sub["accession"],
          sub["items"] or None, sub["primary_doc"], url, body, exhibit])
    return cur.rowcount or 0


def save_insider(cur, ticker, sub, transactions):
    if not transactions:
        return 0
    rows = [(ticker.upper(), sub["accession"], t["seq"], sub["filed"],
             t.get("transaction_date"), t.get("insider_name"), t.get("insider_title"),
             t.get("code"), t.get("acquired"), t.get("shares"), t.get("price"),
             t.get("value"), Json(t)) for t in transactions]
    execute_values(cur, """
        INSERT INTO insider_trades (ticker, accession, seq, filed_date, transaction_date,
                                    insider_name, insider_title, code, acquired, shares,
                                    price, value, raw)
        VALUES %s ON CONFLICT (accession, seq) DO NOTHING
    """, rows, page_size=max(len(rows), 1))
    return cur.rowcount or 0


# --------------------------------------------------------------------------- main
def fetch_filings(tickers=None, days=7, verbose=True):
    """Incremental fetch: 8-K/dilution filings (with text) + Form 4 transactions."""
    tickers = tickers or TOP_50_TICKERS
    ensure_tables()
    session = edgar_session()
    cik_map = load_cik_map(session)
    since = date.today() - timedelta(days=days)

    conn = get_db_connection()
    stats = {"filings": 0, "insider": 0, "failed": []}
    for idx, tk in enumerate(tickers, 1):
        cik = cik_map.get(tk.upper())
        if not cik:
            continue
        if verbose:
            print(f"[{idx}/{len(tickers)}] {tk} ...", flush=True)
        try:
            with conn.cursor() as cur:
                # skip already-stored accessions up front (idempotent incremental)
                cur.execute("SELECT accession FROM filings WHERE ticker=%s AND filed_date>=%s",
                            [tk.upper(), since])
                have_f = {r[0] for r in cur.fetchall()}
                cur.execute("SELECT DISTINCT accession FROM insider_trades WHERE ticker=%s AND filed_date>=%s",
                            [tk.upper(), since])
                have_i = {r[0] for r in cur.fetchall()}
                for sub in recent_submissions(session, cik, since):
                    if sub["form"] in FILING_FORMS and sub["accession"] not in have_f:
                        body, exhibit = fetch_filing_texts(session, cik, sub["accession"],
                                                           sub["primary_doc"])
                        stats["filings"] += save_filing(cur, tk, cik, sub, body, exhibit)
                    elif sub["form"] in INSIDER_FORMS and sub["accession"] not in have_i:
                        acc_nodash = sub["accession"].replace("-", "")
                        doc = sub["primary_doc"]
                        # primaryDocument for Form 4 is usually "xslF345X05/<file>.xml" (the
                        # styled rendering); the RAW ownership XML is the bare basename.
                        xml_doc = doc.split("/")[-1]
                        if not xml_doc.endswith(".xml"):
                            xml_doc = re.sub(r"\.\w+$", ".xml", xml_doc)
                        try:
                            r = _get(session, ARCHIVES_URL.format(cik=cik, acc_nodash=acc_nodash,
                                                                  doc=xml_doc))
                            stats["insider"] += save_insider(cur, tk, sub, parse_form4(r.text))
                        except Exception as e:
                            print(f"    ⚠ form4 fetch failed {sub['accession']}: {e}")
            conn.commit()
        except Exception as e:
            conn.rollback()
            stats["failed"].append(tk)
            print(f"  ✗ {tk}: {type(e).__name__}: {e}")
            logger.error("FILINGS_FETCH_ERROR", f"filings failed for {tk}",
                         details={"ticker": tk, "error": str(e)})
    conn.close()
    if verbose:
        print(f"✓ filings: +{stats['filings']} rows, insider: +{stats['insider']} rows, "
              f"failed: {stats['failed'] or 'none'}")
    logger.info("FILINGS_FETCH_COMPLETE", f"+{stats['filings']} filings, +{stats['insider']} insider",
                details=stats)
    return stats


def show_stats():
    conn = get_db_connection()
    with conn.cursor() as cur:
        for tbl in ("filings", "insider_trades"):
            cur.execute(f"SELECT count(*), min(filed_date), max(filed_date), count(DISTINCT ticker) FROM {tbl}")
            n, lo, hi, tks = cur.fetchone()
            print(f"  {tbl:15s} n={n:6d}  {lo} .. {hi}  tickers={tks}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EDGAR 8-K/press-release + Form 4 fetcher (9.1/9.2)")
    ap.add_argument("--tickers", nargs="+")
    ap.add_argument("--backfill-days", type=int, default=None,
                    help="history window (default: 7-day incremental)")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    if args.stats:
        ensure_tables()
        show_stats()
    else:
        fetch_filings(tickers=args.tickers, days=args.backfill_days or 7)
        show_stats()
