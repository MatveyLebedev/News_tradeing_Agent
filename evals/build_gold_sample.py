"""Gold-set sample for the fine-tuning pre-checks (E2 test set T2 + E1 graph-only audit).

Draws ~300 articles from the live universe's news corpus and expands each into
article × candidate pairs for hand labeling (`docs/GOLD_LABELING_GUIDE.md`). Read-only.

Strata (article-level, deduped in this order):
  graph_only   120 — pair reached the corpus ONLY via graph AFFECTS (regex missed the name);
                     40 per universe fan-out bin (1 / 2-4 / ≥5 universe tickers on the
                     article). These double as the E1 step-4 audit.
  random       100 — uniform over all (article, ticker) corpus pairs.
  multi_ticker  80 — articles in the corpus of ≥3 universe tickers.
Per-ticker cap of 3 sampled pairs per stratum, so mega-caps do not fill the set.

Candidates per article: universe tickers whose corpus holds it, plus every ticker its AFFECTS
edges name (any event type, any confidence — what extraction produced), capped at 6: the
sampled (anchor) ticker, then other universe tickers in random order, then non-universe
tickers by confidence. Plus ONE sector peer of the anchor that is not already a candidate
(a deliberate distractor).

Outputs to longterm_agent/exports/gold/ (gitignored — news text never enters the repo):
  gold_sheet.csv     blind sheet: no stratum, no source, no graph sentiment
  gold_key.csv       item_id → hidden metadata, joined only at scoring time
  label_gold.html    offline labeling tool over the same items (saves to localStorage, exports CSV)
  manifest.json      seed, window, universe, counts, git revision

    python -m evals.build_gold_sample            # build
    python -m evals.build_gold_sample --dry-run  # pool sizes only
"""
import argparse
import base64
import csv
import json
import random
import subprocess
import typing
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from core.db import fetch_all
from graph_db.event_extractor import EventType
from graph_db.neo4j_manager import Neo4jManager
from longterm_agent.company_names import names_for
from longterm_agent.sector_map import peers_for
from longterm_agent.tools.news_corpus_tool import _affects_news_ids, _name_regex

SEED = 20261022
WINDOW = ("2026-03-01 00:00:00", "2026-09-21 23:59:59")  # ET-naive, as stored
UNIVERSE_SINCE = "2026-07-13"   # first week the live batch ran the full universe
N_GRAPH_ONLY_PER_BIN = 40
N_RANDOM = 100
N_MULTI = 80
PER_TICKER_CAP = 3
MAX_CANDIDATES = 6
OUT = Path(__file__).resolve().parents[1] / "longterm_agent" / "exports" / "gold"
SENT_CODES = ["positive", "negative", "neutral", "bullish", "bearish"]
EVENT_TYPES = list(typing.get_args(EventType))


def universe():
    rows = fetch_all("SELECT DISTINCT ticker FROM longterm_agent_results WHERE as_of >= %s "
                     "ORDER BY ticker", (UNIVERSE_SINCE,))
    return [r[0] for r in rows]


def windowed_ids(ids):
    """Subset of ids published inside WINDOW, in batches (the remote link drops big results)."""
    ids = sorted(ids)
    keep = set()
    for i in range(0, len(ids), 2000):
        chunk = ids[i:i + 2000]
        rows = fetch_all("SELECT id FROM news WHERE id = ANY(%s) AND published BETWEEN %s AND %s",
                         (chunk, *WINDOW))
        keep.update(r[0] for r in rows)
    return keep


def regex_ids(ticker):
    rx = _name_regex(ticker)
    if rx is None:
        return set()
    rows = fetch_all("SELECT id FROM news WHERE published BETWEEN %s AND %s "
                     "AND (title ~* %s OR summary ~* %s)", (*WINDOW, rx, rx))
    return {r[0] for r in rows}


def build_pool(tickers, neo):
    """pairs[(news_id, ticker)] = {"sources": set, "graph_sentiment": str|None}"""
    pairs = {}
    for i, tk in enumerate(tickers, 1):
        gmap = _affects_news_ids(tk, neo, 0.5)
        g_in = windowed_ids(gmap.keys())
        r_in = regex_ids(tk)
        for nid in g_in | r_in:
            src = set()
            if nid in g_in:
                src.add("graph")
            if nid in r_in:
                src.add("ilike")
            pairs[(nid, tk)] = {"sources": src, "graph_sentiment": gmap.get(nid)}
        print(f"  [{i:2d}/{len(tickers)}] {tk:6s} graph={len(g_in):5d} regex={len(r_in):5d}")
    return pairs


def capped_sample(rng, items, n, key_ticker, taken_articles, cap=PER_TICKER_CAP):
    """Draw up to n items in random order, skipping taken articles and full tickers."""
    items = list(items)
    rng.shuffle(items)
    per_tk, out = Counter(), []
    for it in items:
        nid, tk = it[0], key_ticker(it)
        if nid in taken_articles or per_tk[tk] >= cap:
            continue
        out.append(it)
        per_tk[tk] += 1
        taken_articles.add(nid)
        if len(out) >= n:
            break
    return out


def fetch_texts(ids):
    """{id: (id, published, source, title, summary)}.

    Text travels base64-encoded: over the remote link, plain-text result sets with article
    bodies stall or get the connection reset mid-transfer (the full `news` scan in
    scripts/scan_reaction_news.py fails the same way), while the same rows base64-encoded
    arrive in ~1s. Ids and numbers are unaffected.
    """
    sep = "\x1f"
    out = {}
    for i in range(0, len(ids), 200):
        rows = fetch_all(
            "SELECT id, published, encode(convert_to(coalesce(source,'') || chr(31) || "
            "coalesce(title,'') || chr(31) || coalesce(summary,''), 'UTF8'), 'base64') "
            "FROM news WHERE id = ANY(%s)", (ids[i:i + 200],))
        for nid, published, b64 in rows:
            source, title, summary = base64.b64decode(b64).decode("utf-8").split(sep, 2)
            out[nid] = (nid, published, source, title, summary)
    return out


def affects_for(news_ids, neo):
    """All AFFECTS edges on these articles: {news_id: [(ticker, sentiment, confidence, type)]}."""
    out = defaultdict(list)
    ids = sorted(news_ids)
    for i in range(0, len(ids), 50):
        # Sentiment and event type come back as integer CODES, not strings: over the remote
        # link, result sets carrying those string values stall until the bolt connection goes
        # defunct (bisected 2026-10-22 — the same query returning ids, tickers and confidences
        # streams in 0.5s). Same cleartext-payload problem as fetch_texts.
        rows = neo.query(
            """
            UNWIND $ids AS nid
            MATCH (a:Article {news_id: nid})<-[:SOURCED_FROM]-(e:Event)-[r:AFFECTS]->(c:Company)
            RETURN a.news_id AS nid, c.ticker AS tk,
                   coalesce([k IN range(0, size($sents) - 1) WHERE $sents[k] = r.sentiment][0], -1) AS s,
                   coalesce(r.confidence, 0) AS conf,
                   coalesce([k IN range(0, size($types) - 1) WHERE $types[k] = e.type][0], -1) AS et
            """, {"ids": ids[i:i + 50], "sents": SENT_CODES, "types": EVENT_TYPES})
        for r in rows:
            s = SENT_CODES[r["s"]] if r["s"] >= 0 else "other"
            et = EVENT_TYPES[r["et"]] if r["et"] >= 0 else "other"
            out[int(r["nid"])].append((r["tk"], s, float(r["conf"]), et))
    return out


def git_rev():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rng = random.Random(SEED)
    tickers = universe()
    uni = set(tickers)
    print(f"universe: {len(tickers)} tickers; window {WINDOW[0][:10]} .. {WINDOW[1][:10]}")
    neo = Neo4jManager()
    pairs = build_pool(tickers, neo)

    by_article = defaultdict(set)
    for (nid, tk) in pairs:
        by_article[nid].add(tk)
    graph_deg = Counter(nid for (nid, tk), v in pairs.items() if "graph" in v["sources"])

    def deg_bin(nid):
        d = graph_deg[nid]
        return "1" if d <= 1 else ("2-4" if d <= 4 else "5+")

    graph_only = [(nid, tk) for (nid, tk), v in pairs.items() if v["sources"] == {"graph"}]
    multi = [nid for nid, tks in by_article.items() if len(tks) >= 3]
    pool_stats = {
        "pairs": len(pairs), "articles": len(by_article),
        "graph_only_pairs": len(graph_only),
        "graph_only_by_bin": dict(Counter(deg_bin(n) for n, _ in graph_only)),
        "multi_ticker_articles": len(multi),
        "pairs_by_source": dict(Counter("+".join(sorted(v["sources"])) for v in pairs.values())),
    }
    print(json.dumps(pool_stats, indent=2))
    if args.dry_run:
        return

    # --- strata, in a fixed order so dedup is deterministic -----------------------------
    taken = set()
    sampled = []  # (stratum, news_id, anchor_ticker)
    for b in ("1", "2-4", "5+"):
        cand = sorted(p for p in graph_only if deg_bin(p[0]) == b)
        for nid, tk in capped_sample(rng, cand, N_GRAPH_ONLY_PER_BIN, lambda it: it[1], taken):
            sampled.append(("graph_only", nid, tk))
    for nid, tk in capped_sample(rng, sorted(pairs), N_RANDOM, lambda it: it[1], taken):
        sampled.append(("random", nid, tk))
    # anchor = a random member, not the alphabetical first (which would always cap AAPL/AMD)
    multi_items = [(nid, rng.choice(sorted(by_article[nid]))) for nid in sorted(multi)]
    for nid, tk in capped_sample(rng, multi_items, N_MULTI, lambda it: it[1], taken):
        sampled.append(("multi_ticker", nid, tk))

    ids = [nid for _, nid, _ in sampled]
    texts = fetch_texts(ids)
    aff = affects_for(ids, neo)

    # --- expand into article × candidate items ------------------------------------------
    items, keys = [], []
    article_order = list(range(len(sampled)))
    rng.shuffle(article_order)
    for a_idx, s_idx in enumerate(article_order, 1):
        stratum, nid, anchor = sampled[s_idx]
        _, published, source, title, summary = texts[nid]
        edges = aff.get(nid, [])
        best = {}
        for tk, s, conf, et in edges:
            if tk and (tk not in best or conf > best[tk][1]):
                best[tk] = (s, conf, et)
        # anchor always kept; the rest in random order, so truncation is not alphabetical
        # (sorted() kept AAPL/AMD/AMZN on every wide macro article)
        rest = sorted(by_article[nid] - {anchor})
        rng.shuffle(rest)
        uni_c = [anchor] + rest
        other_c = sorted((t for t in best if t not in uni), key=lambda t: (-best[t][1], t))
        cands = (uni_c + other_c)[:MAX_CANDIDATES]
        truncated = len(uni_c) + len(other_c) - len(cands)
        peers = [p for p in peers_for(anchor) if p not in cands and p not in best]
        distractor = rng.choice(peers) if peers else None
        if distractor:
            cands.append(distractor)
        cand_order = list(cands)
        rng.shuffle(cand_order)
        for c_idx, tk in enumerate(cand_order, 1):
            item_id = f"A{a_idx:03d}-{c_idx}"
            pv = pairs.get((nid, tk), {})
            items.append({
                "item_id": item_id, "news_id": nid,
                "published": published.strftime("%Y-%m-%d %H:%M") if published else "",
                "source": source or "", "title": (title or "").strip(),
                "summary": (summary or "").strip(),
                "ticker": tk, "names": "; ".join(names_for(tk)),
                "role": "", "sentiment": "", "unsure": "", "note": "",
            })
            keys.append({
                "item_id": item_id, "news_id": nid, "ticker": tk, "stratum": stratum,
                "anchor_ticker": anchor, "is_anchor": tk == anchor,
                "is_distractor": tk == distractor, "in_universe": tk in uni,
                "sources": "+".join(sorted(pv.get("sources", []))),
                "graph_sentiment": pv.get("graph_sentiment") or (best.get(tk, (None,))[0] or ""),
                "affects_conf": best.get(tk, (None, ""))[1],
                "affects_event_type": best.get(tk, (None, None, ""))[2],
                "universe_graph_degree": graph_deg[nid], "affects_degree": len(best),
                "candidates_truncated": truncated,
            })

    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "gold_sheet.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(items[0]))
        w.writeheader()
        w.writerows(items)
    with open(OUT / "gold_key.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(keys[0]))
        w.writeheader()
        w.writerows(keys)
    write_html(items, OUT / "label_gold.html")
    manifest = {
        "built_at": datetime.now().isoformat(timespec="seconds"), "git_rev": git_rev(),
        "seed": SEED, "window": WINDOW, "universe_since": UNIVERSE_SINCE,
        "universe": tickers, "pool": pool_stats,
        "articles_by_stratum": dict(Counter(s for s, _, _ in sampled)),
        "items": len(items), "distractors": sum(k["is_distractor"] for k in keys),
        "guide": "docs/GOLD_LABELING_GUIDE.md",
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    print(json.dumps({k: manifest[k] for k in ("articles_by_stratum", "items", "distractors")},
                     indent=2))
    print(f"→ {OUT}")


HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Gold labeling</title>
<style>
:root{--bg:#fafaf9;--fg:#1c1917;--mut:#78716c;--card:#fff;--bd:#e7e5e4;--acc:#2563eb;--ok:#16a34a}
@media (prefers-color-scheme:dark){:root{--bg:#1c1917;--fg:#f5f5f4;--mut:#a8a29e;--card:#292524;--bd:#44403c;--acc:#60a5fa;--ok:#4ade80}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:16px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:16px;margin:12px 0}
.mut{color:var(--mut);font-size:13px} h2{font-size:19px;margin:6px 0 4px}
.tk{font-size:22px;font-weight:600} .row{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
button{font:inherit;padding:6px 12px;border-radius:8px;border:1px solid var(--bd);background:var(--card);color:var(--fg);cursor:pointer}
button.on{background:var(--acc);color:#fff;border-color:var(--acc)} kbd{font-size:11px;opacity:.7}
input[type=text]{width:100%;box-sizing:border-box;font:inherit;padding:6px;border-radius:8px;border:1px solid var(--bd);background:var(--card);color:var(--fg)}
.bar{height:6px;background:var(--bd);border-radius:3px}.bar>div{height:6px;background:var(--ok);border-radius:3px}
</style></head><body><main>
<div class="row"><b id="pos"></b><span class="mut" id="done"></span><span style="flex:1"></span>
<button id="prev">← prev</button><button id="next">next →</button><button id="nextOpen">next unlabeled</button><button id="export">Export CSV</button></div>
<div class="bar"><div id="prog"></div></div>
<div class="card"><div class="mut" id="meta"></div><h2 id="title"></h2><div id="summary"></div></div>
<div class="card"><div class="tk" id="tk"></div><div class="mut" id="names"></div>
<div class="mut" style="margin-top:10px">Role</div><div class="row" id="roles"></div>
<div class="mut">Sentiment for this company</div><div class="row" id="sents"></div>
<div class="row"><label><input type="checkbox" id="unsure"> unsure <kbd>u</kbd></label></div>
<input type="text" id="note" placeholder="note (optional)"></div>
<p class="mut">Keys: 1 subject · 2 involved · 3 context · 4 not_in_text · q positive · w neutral · e negative · u unsure · ←/→ navigate · Enter next unlabeled. Guide: docs/GOLD_LABELING_GUIDE.md. Labels live in this browser (localStorage) — export the CSV when done.</p>
</main><script>
const ITEMS=__ITEMS__;const KEY="gold_labels_v1";
let L={};try{L=JSON.parse(localStorage.getItem(KEY)||"{}")}catch(e){}
const save=()=>{try{localStorage.setItem(KEY,JSON.stringify(L))}catch(e){}};
const ROLES=[["subject","1"],["involved","2"],["context","3"],["not_in_text","4"]];
const SENTS=[["positive","q"],["neutral","w"],["negative","e"]];
let i=0;const lab=id=>L[id]||(L[id]={role:"",sentiment:"",unsure:false,note:""});
const isDone=it=>{const l=L[it.item_id];return l&&l.role&&(l.role==="not_in_text"||l.sentiment)};
function btns(el,opts,field){el.innerHTML="";for(const [v,k] of opts){const b=document.createElement("button");b.innerHTML=v+" <kbd>"+k+"</kbd>";b.onclick=()=>set(field,v);b.dataset.v=v;el.appendChild(b)}}
btns(roles,ROLES,"role");btns(sents,SENTS,"sentiment");
function set(f,v){const l=lab(ITEMS[i].item_id);l[f]=v;if(f==="role"&&v==="not_in_text")l.sentiment="n/a";if(f==="role"&&v!=="not_in_text"&&l.sentiment==="n/a")l.sentiment="";save();render()}
function render(){const it=ITEMS[i],l=lab(it.item_id);pos.textContent=it.item_id+"  ("+(i+1)+"/"+ITEMS.length+")";
meta.textContent=it.published+" · "+it.source;title.textContent=it.title;summary.textContent=it.summary;
tk.textContent=it.ticker;names.textContent=it.names||"(no curated name)";
for(const b of roles.children)b.className=b.dataset.v===l.role?"on":"";
for(const b of sents.children){b.className=b.dataset.v===l.sentiment?"on":"";b.disabled=l.role==="not_in_text"}
unsure.checked=!!l.unsure;note.value=l.note||"";const d=ITEMS.filter(isDone).length;
done.textContent=d+" labeled";prog.style.width=(100*d/ITEMS.length)+"%"}
function go(k){i=Math.max(0,Math.min(ITEMS.length-1,k));render()}
function nextOpen(){for(let k=1;k<=ITEMS.length;k++){const j=(i+k)%ITEMS.length;if(!isDone(ITEMS[j]))return go(j)}}
prev.onclick=()=>go(i-1);next.onclick=()=>go(i+1);document.getElementById("nextOpen").onclick=nextOpen;
unsure.onchange=()=>{lab(ITEMS[i].item_id).unsure=unsure.checked;save()};
note.oninput=()=>{lab(ITEMS[i].item_id).note=note.value;save()};
document.addEventListener("keydown",e=>{if(e.target===note)return;const k=e.key;
for(const [v,c] of ROLES)if(k===c)return set("role",v);for(const [v,c] of SENTS)if(k===c)return set("sentiment",v);
if(k==="u"){unsure.checked=!unsure.checked;unsure.onchange()}if(k==="ArrowLeft")go(i-1);if(k==="ArrowRight")go(i+1);if(k==="Enter")nextOpen()});
document.getElementById("export").onclick=()=>{const q=s=>'"'+String(s??"").replace(/"/g,'""')+'"';
const rows=[["item_id","news_id","ticker","role","sentiment","unsure","note"].join(",")];
for(const it of ITEMS){const l=L[it.item_id]||{};rows.push([it.item_id,it.news_id,it.ticker,l.role||"",l.sentiment||"",l.unsure?"1":"",l.note||""].map(q).join(","))}
const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([rows.join("\\n")],{type:"text/csv"}));a.download="gold_labels.csv";a.click()};
render();
</script></body></html>"""


def write_html(items, path):
    slim = [{k: it[k] for k in ("item_id", "news_id", "published", "source", "title", "summary",
                                "ticker", "names")} for it in items]
    data = json.dumps(slim, ensure_ascii=False).replace("</", "<\\/")
    path.write_text(HTML.replace("__ITEMS__", data), encoding="utf-8")


if __name__ == "__main__":
    main()
