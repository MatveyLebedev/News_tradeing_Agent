"""
Phase 2 — ablation: does regex + embeddings (hybrid) beat each modality alone?

Models (sklearn): base-rate, source-only, regex, emb(PCA-128), meta(graph+ticker+struct),
hybrid-early (concat -> HistGBM), hybrid-late (stack of the three via LogReg).
Temporal split (train->val->test) from features_tabular.parquet; pairs grouped by news.
Metrics on test: PR-AUC (primary, +bootstrap CI by news), ROC-AUC, recall@budget, Brier.
Plus article-level gate metric on the focused subset.
"""
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.preprocessing import normalize, StandardScaler
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.utils.class_weight import compute_sample_weight
from sklearn.metrics import average_precision_score, roc_auc_score, brier_score_loss

warnings.filterwarnings("ignore")
rng = np.random.default_rng(0)
D = Path("/Users/lebedevmatvey/test/significance/data")

from regex_featurizer import RegexFeaturizer  # noqa: E402
REGEX_COLS = RegexFeaturizer().feature_names
GP = ["g_affects_conf", "g_sentiment", "g_sent_net", "g_n_events", "g_from_single_evt", "g_min_evt_companies"]
TK = ["tk_price", "tk_disp", "tk_log_vol"]
STRUCT = ["tod_mins_since_open", "tod_mins_to_close", "tod_first_hour", "tod_last_hour",
          "dow", "source_code", "n_priced_tickers", "log_n_companies"]


def hgb():
    return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.06, max_leaf_nodes=31,
                                          l2_regularization=1.0, early_stopping=True,
                                          validation_fraction=0.15, random_state=0)


def recall_at_budget(y, s, budgets=(0.1, 0.2, 0.3)):
    order = np.argsort(-s)
    y = np.asarray(y)[order]
    P = y.sum()
    out = {}
    for b in budgets:
        k = max(1, int(len(y) * b))
        out[b] = y[:k].sum() / P if P else 0.0
    # budget needed to reach 80% / 90% recall
    cum = np.cumsum(y) / P if P else np.zeros(len(y))
    for r in (0.8, 0.9):
        idx = np.argmax(cum >= r)
        out[f"bud@{int(r*100)}"] = (idx + 1) / len(y) if cum[-1] >= r else 1.0
    return out


def bootstrap_ap(news, y, s, n=300):
    df = pd.DataFrame({"news": news, "y": y, "s": s})
    groups = {nid: g.index.values for nid, g in df.groupby("news")}
    uids = np.array(list(groups.keys()))
    aps = []
    for _ in range(n):
        samp = rng.choice(uids, size=len(uids), replace=True)
        idx = np.concatenate([groups[u] for u in samp])
        yy = df["y"].values[idx]
        if yy.sum() == 0 or yy.sum() == len(yy):
            continue
        aps.append(average_precision_score(yy, df["s"].values[idx]))
    lo, hi = np.percentile(aps, [2.5, 97.5])
    return lo, hi


def main():
    df = pd.read_parquet(D / "features_tabular.parquet")
    df = df[df.has_emb == 1].reset_index(drop=True)
    GA = [c for c in df.columns if c.startswith("ga_")]
    META = GP + GA + TK + STRUCT

    # embeddings -> L2 -> PCA128 (fit on unique TRAIN news)
    z = np.load(D / "embeddings.npz")
    idmap = {int(n): i for i, n in enumerate(z["news_id"])}
    E = normalize(z["emb"].astype("float32"))
    tr_news = df.loc[df.split == "train", "news_id"].unique()
    pca = PCA(n_components=128, random_state=0).fit(E[[idmap[n] for n in tr_news]])
    Ep = pca.transform(E)
    emb_of = lambda sub: np.vstack([Ep[idmap[n]] for n in sub.news_id])

    tr, va, te = (df[df.split == s].reset_index(drop=True) for s in ["train", "val", "test"])
    ytr, yva, yte = tr.label.values, va.label.values, te.label.values
    wtr = compute_sample_weight("balanced", ytr)
    print(f"train={len(tr)} ({ytr.mean()*100:.1f}%)  val={len(va)} ({yva.mean()*100:.1f}%)  "
          f"test={len(te)} ({yte.mean()*100:.1f}%)")

    def mats(cols):
        return tr[cols].values, va[cols].values, te[cols].values

    Xtr_e, Xva_e, Xte_e = emb_of(tr), emb_of(va), emb_of(te)
    results, test_scores = {}, {}

    def record(name, ste, sva=None):
        m = {"PR_AUC": average_precision_score(yte, ste),
             "ROC_AUC": roc_auc_score(yte, ste),
             "Brier": brier_score_loss(yte, np.clip(ste, 0, 1))}
        m.update(recall_at_budget(yte, ste))
        results[name] = m
        test_scores[name] = ste

    # 0) baselines
    record("base_rate", np.full(len(te), ytr.mean()))
    src_rate = tr.groupby("source").label.mean()
    record("source_only", te.source.map(src_rate).fillna(ytr.mean()).values)

    # helper: scaled logreg / hgb on given matrices
    def run_logreg(Xtr, Xte, name):
        sc = StandardScaler().fit(Xtr)
        lr = LogisticRegression(max_iter=2000, class_weight="balanced", C=1.0)
        lr.fit(sc.transform(Xtr), ytr)
        record(name, lr.predict_proba(sc.transform(Xte))[:, 1])

    def run_hgb(Xtr, Xva, Xte, name):
        m = hgb().fit(Xtr, ytr, sample_weight=wtr)
        record(name, m.predict_proba(Xte)[:, 1])
        return m.predict_proba(Xva)[:, 1], m.predict_proba(Xte)[:, 1]

    # 1) regex
    rtr, rva, rte = mats(REGEX_COLS)
    run_logreg(rtr, rte, "regex_logreg")
    pr_va, pr_te = run_hgb(rtr, rva, rte, "regex_hgb")
    # 2) emb
    run_logreg(Xtr_e, Xte_e, "emb_logreg")
    pe_va, pe_te = run_hgb(Xtr_e, Xva_e, Xte_e, "emb_hgb")
    # 3) meta
    mtr, mva, mte = mats(META)
    pm_va, pm_te = run_hgb(mtr, mva, mte, "meta_hgb")
    # 4) hybrid early (concat everything)
    Htr = np.hstack([rtr, Xtr_e, mtr]); Hva = np.hstack([rva, Xva_e, mva]); Hte = np.hstack([rte, Xte_e, mte])
    run_hgb(Htr, Hva, Hte, "hybrid_early_hgb")
    # 5) hybrid late (stack regex/emb/meta probs via logreg on VAL)
    Sva = np.column_stack([pr_va, pe_va, pm_va]); Ste = np.column_stack([pr_te, pe_te, pm_te])
    stk = LogisticRegression(max_iter=2000, class_weight="balanced").fit(Sva, yva)
    record("hybrid_late_stack", stk.predict_proba(Ste)[:, 1])

    # --- table ---
    res = pd.DataFrame(results).T
    order = ["base_rate", "source_only", "regex_logreg", "regex_hgb", "emb_logreg", "emb_hgb",
             "meta_hgb", "hybrid_early_hgb", "hybrid_late_stack"]
    res = res.reindex(order)
    print("\n=== TEST metrics (pair-level) ===")
    cols = ["PR_AUC", "ROC_AUC", 0.1, 0.2, 0.3, "bud@80", "Brier"]
    hdr = f"{'model':<18} {'PR-AUC':>7} {'ROC':>6} {'r@10%':>6} {'r@20%':>6} {'r@30%':>6} {'bud@80':>7} {'Brier':>6}"
    print(hdr); print("-" * len(hdr))
    base = res.loc["base_rate", "PR_AUC"]
    for n in order:
        r = res.loc[n]
        print(f"{n:<18} {r['PR_AUC']:>7.3f} {r['ROC_AUC']:>6.3f} {r[0.1]:>6.2f} {r[0.2]:>6.2f} "
              f"{r[0.3]:>6.2f} {r['bud@80']:>7.2f} {r['Brier']:>6.3f}")
    print(f"\nbase rate (test pos) = {yte.mean():.3f}  | PR-AUC lift over base: "
          f"hybrid_early ×{res.loc['hybrid_early_hgb','PR_AUC']/base:.1f}")

    # --- bootstrap CI for key models ---
    print("\n=== PR-AUC 95% CI (bootstrap by news) ===")
    for n in ["regex_hgb", "emb_hgb", "meta_hgb", "hybrid_early_hgb", "hybrid_late_stack"]:
        lo, hi = bootstrap_ap(te.news_id.values, yte, test_scores[n])
        print(f"  {n:<18} {res.loc[n,'PR_AUC']:.3f}  [{lo:.3f}, {hi:.3f}]")

    # --- article-level gate metric on focused subset (<=3 graph companies) ---
    print("\n=== article-level gate (focused: ga_n_companies<=3) ===")
    foc = te[te.ga_n_companies <= 3].copy()
    for n in ["meta_hgb", "hybrid_early_hgb"]:
        foc["s"] = test_scores[n][te.ga_n_companies.values <= 3]
        art = foc.groupby("news_id").agg(y=("label", "max"), s=("s", "max"))
        if art.y.nunique() > 1:
            ra = recall_at_budget(art.y.values, art.s.values)
            print(f"  {n:<18} articles={len(art)} pos={art.y.mean()*100:.0f}%  "
                  f"PR-AUC={average_precision_score(art.y, art.s):.3f}  r@30%={ra[0.3]:.2f}")

    res.to_csv(D / "phase2_results.csv")
    print(f"\n[write] {D/'phase2_results.csv'}")


if __name__ == "__main__":
    main()
