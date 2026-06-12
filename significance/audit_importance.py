"""Diagnostic: how much does each MODALITY actually contribute, and is meta leaky?
Group-permutation importance on the hybrid model + permutation importance within meta."""
import warnings
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.preprocessing import normalize
from sklearn.decomposition import PCA
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.utils.class_weight import compute_sample_weight
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score as AP

warnings.filterwarnings("ignore")
rng = np.random.default_rng(0)
D = Path("/Users/lebedevmatvey/test/significance/data")
from regex_featurizer import RegexFeaturizer
REGEX = RegexFeaturizer().feature_names
GP = ["g_affects_conf", "g_sentiment", "g_sent_net", "g_n_events", "g_from_single_evt", "g_min_evt_companies"]
TK = ["tk_price", "tk_disp", "tk_log_vol"]
STRUCT = ["tod_mins_since_open", "tod_mins_to_close", "tod_first_hour", "tod_last_hour",
          "dow", "source_code", "n_priced_tickers", "log_n_companies"]


def hgb():
    return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.06, max_leaf_nodes=31,
                                          l2_regularization=1.0, early_stopping=True,
                                          validation_fraction=0.15, random_state=0)


df = pd.read_parquet(D / "features_tabular.parquet")
df = df[df.has_emb == 1].reset_index(drop=True)
GA = [c for c in df.columns if c.startswith("ga_")]
META = GP + GA + TK + STRUCT
z = np.load(D / "embeddings.npz")
idmap = {int(n): i for i, n in enumerate(z["news_id"])}
E = normalize(z["emb"].astype("float32"))
tr_news = df.loc[df.split == "train", "news_id"].unique()
pca = PCA(128, random_state=0).fit(E[[idmap[n] for n in tr_news]])
Ep = pca.transform(E)
emb_of = lambda s: np.vstack([Ep[idmap[n]] for n in s.news_id])
tr, te = df[df.split == "train"].reset_index(drop=True), df[df.split == "test"].reset_index(drop=True)
ytr, yte = tr.label.values, te.label.values
w = compute_sample_weight("balanced", ytr)

Htr = np.hstack([tr[REGEX].values, emb_of(tr), tr[META].values])
Hte = np.hstack([te[REGEX].values, emb_of(te), te[META].values])
sl = {"regex": slice(0, 30), "emb": slice(30, 158), "meta": slice(158, 158 + len(META))}
mdl = hgb().fit(Htr, ytr, sample_weight=w)
base = AP(yte, mdl.predict_proba(Hte)[:, 1])
print(f"hybrid PR-AUC (test) = {base:.3f}\n=== group-permutation: PR-AUC drop when modality is destroyed ===")
for g, s in sl.items():
    drops = []
    for _ in range(8):
        Xp = Hte.copy(); p = rng.permutation(len(Hte)); Xp[:, s] = Hte[p][:, s]
        drops.append(base - AP(yte, mdl.predict_proba(Xp)[:, 1]))
    print(f"  {g:<6} ΔPR-AUC = {np.mean(drops):+.3f}  (bigger drop = more it matters)")

# within-meta importance
mm = hgb().fit(tr[META].values, ytr, sample_weight=w)
imp = permutation_importance(mm, te[META].values, yte, scoring="average_precision",
                             n_repeats=10, random_state=0)
print("\n=== top meta features (permutation importance, AP) ===")
for i in np.argsort(imp.importances_mean)[::-1][:12]:
    print(f"  {META[i]:<26} {imp.importances_mean[i]:+.4f}")
