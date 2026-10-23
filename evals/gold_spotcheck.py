"""Human spot-check of the LLM-labeled gold set (docs/PLAN_FINETUNE_PRECHECKS_2026-10-20.md, T2).

The gold set was labeled by Claude. Before it is trusted for scoring other models, a person
blind-labels a subset and we measure agreement. A uniform draw would be ~91% `not_in_text`
and agreement would look high for free, so the subset is stratified ON THE LLM's LABELS —
the page never shows them, so the person stays blind:

  mention      60 — items the LLM called subject / involved / context
  graph_only   60 — graph-only corpus pairs the LLM called not_in_text (the headline finding)
  random       30 — uniform over everything else

Agreement is reported per stratum and re-weighted to the full set's composition, since the
stratification over-samples mentions on purpose.

    python -m evals.gold_spotcheck build   # → longterm_agent/exports/gold/spotcheck/spotcheck.html
    python -m evals.gold_spotcheck score   # reads spotcheck_labels.csv exported from the page
"""
import argparse
import csv
import json
import random
from collections import Counter, defaultdict

from evals.build_gold_sample import OUT, write_html

SEED = 20261023
N = {"mention": 60, "graph_only": 60, "random": 30}
DIR = OUT / "spotcheck"
STORAGE_KEY = "gold_spotcheck_v1"   # distinct from the full labeler's key on the same origin


def _load():
    sheet = {r["item_id"]: r for r in csv.DictReader(open(OUT / "gold_sheet.csv", encoding="utf-8"))}
    key = {r["item_id"]: r for r in csv.DictReader(open(OUT / "gold_key.csv", encoding="utf-8"))}
    llm = {r["item_id"]: r for r in csv.DictReader(open(OUT / "gold_labels_claude.csv", encoding="utf-8"))}
    return sheet, key, llm


def stratum_of(item_id, key, llm):
    if llm[item_id]["role"] != "not_in_text":
        return "mention"
    if key[item_id]["sources"] == "graph" and key[item_id]["in_universe"] == "True":
        return "graph_only"
    return "random"


def build():
    sheet, key, llm = _load()
    rng = random.Random(SEED)
    pools = defaultdict(list)
    for iid in sorted(sheet):
        pools[stratum_of(iid, key, llm)].append(iid)
    picked = []
    for s, n in N.items():
        picked += [(s, iid) for iid in rng.sample(pools[s], n)]
    rng.shuffle(picked)
    DIR.mkdir(parents=True, exist_ok=True)
    items = [sheet[iid] for _, iid in picked]
    write_html(items, DIR / "spotcheck.html", storage_key=STORAGE_KEY,
               export_name="spotcheck_labels.csv")
    with open(DIR / "spotcheck_key.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["item_id", "stratum"])
        w.writerows((iid, s) for s, iid in picked)
    pop = {s: len(v) for s, v in pools.items()}
    (DIR / "manifest.json").write_text(json.dumps(
        {"seed": SEED, "n": N, "population": pop, "storage_key": STORAGE_KEY}, indent=2))
    print(f"{len(items)} items → {DIR / 'spotcheck.html'}; population by stratum {pop}")


def _kappa(pairs):
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else 1.0


def score(path):
    _, key, llm = _load()
    strata = {r["item_id"]: r["stratum"] for r in csv.DictReader(open(DIR / "spotcheck_key.csv"))}
    pop = json.loads((DIR / "manifest.json").read_text())["population"]
    human = {r["item_id"]: r for r in csv.DictReader(open(path, encoding="utf-8")) if r["role"]}
    missing = sorted(set(strata) - set(human))
    print(f"human-labeled {len(human)}/{len(strata)}" + (f"; missing e.g. {missing[:5]}" if missing else ""))

    def mention(role):
        return role in ("subject", "involved", "context")

    per = {}
    for s in N:
        ids = [i for i in strata if strata[i] == s and i in human]
        role = [(llm[i]["role"], human[i]["role"]) for i in ids]
        both_mention = [i for i in ids if mention(llm[i]["role"]) and mention(human[i]["role"])]
        sent = [(llm[i]["sentiment"], human[i]["sentiment"]) for i in both_mention]
        per[s] = {
            "n": len(ids),
            "role_agree": sum(a == b for a, b in role) / len(ids) if ids else None,
            "mention_agree": sum(mention(a) == mention(b) for a, b in role) / len(ids) if ids else None,
            "sent_agree": sum(a == b for a, b in sent) / len(sent) if sent else None,
            "sent_sign_flips": sum({a, b} == {"positive", "negative"} for a, b in sent),
            "n_sent": len(sent),
        }
        print(f"  {s:10s} n={per[s]['n']:3d} role={per[s]['role_agree']} mention={per[s]['mention_agree']}"
              f" sentiment={per[s]['sent_agree']} (n={len(sent)}, sign flips {per[s]['sent_sign_flips']})")
    tot = sum(pop.values())
    w = {s: pop[s] / tot for s in N}
    for m in ("role_agree", "mention_agree"):
        if all(per[s][m] is not None for s in N):
            print(f"  population-weighted {m}: {sum(w[s] * per[s][m] for s in N):.3f}")
    all_ids = [i for i in strata if i in human]
    print("  kappa role (4-class):", _kappa([(llm[i]["role"], human[i]["role"]) for i in all_ids]),
          "| kappa mention vs not:", _kappa([(mention(llm[i]["role"]), mention(human[i]["role"])) for i in all_ids]))
    gk = [i for i in all_ids if strata[i] == "graph_only"]
    if gk:
        print(f"  graph_only items the human ALSO calls not_in_text: "
              f"{sum(human[i]['role'] == 'not_in_text' for i in gk)}/{len(gk)}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sc = sub.add_parser("score")
    sc.add_argument("--labels", default=str(DIR / "spotcheck_labels.csv"))
    args = ap.parse_args()
    build() if args.cmd == "build" else score(args.labels)


if __name__ == "__main__":
    main()
