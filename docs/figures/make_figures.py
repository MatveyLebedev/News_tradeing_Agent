"""Render the README figures — light and dark variant of each — from numbers already recorded.

Every value below is copied from a write-up in this repository, cited next to it. Nothing is
recomputed here and nothing comes from the DB: the figures illustrate documented results, and
if a source document is corrected, the number here must be corrected with it.

    python docs/figures/make_figures.py        # writes docs/figures/<name>-{light,dark}.png
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT = Path(__file__).resolve().parent

# Reference palette (dataviz skill, references/palette.md): categorical slots 1-2, status
# colours, chrome. Slots 1-3 are documented as passing CVD checks all-pairs in both modes.
THEMES = {
    "light": dict(
        surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", muted="#898781",
        grid="#e1e0d9", axis="#c3c2b7", s1="#2a78d6", s2="#eb6834",
        neutral="#b9b8b2",
    ),
    "dark": dict(
        surface="#1a1a19", ink="#f0efec", ink2="#c3c2b7", muted="#898781",
        grid="#2c2c2a", axis="#383835", s1="#3987e5", s2="#d95926",
        neutral="#5c5b57",
    ),
}
GOOD, CRITICAL = "#0ca30c", "#d03b3b"  # status — never themed, always paired with a label

FONT = ["-apple-system", "Helvetica Neue", "Segoe UI", "DejaVu Sans", "sans-serif"]


def setup(t, w=9.0, h=4.6, ncols=1):
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": FONT, "font.size": 10.5,
        "axes.edgecolor": t["axis"], "axes.labelcolor": t["ink2"],
        "xtick.color": t["muted"], "ytick.color": t["ink2"],
        "axes.titlecolor": t["ink"], "text.color": t["ink"],
    })
    fig, axes = plt.subplots(1, ncols, figsize=(w, h), facecolor=t["surface"])
    for ax in (axes if ncols > 1 else [axes]):
        ax.set_facecolor(t["surface"])
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_color(t["axis"])
        ax.spines["bottom"].set_color(t["axis"])
        ax.tick_params(length=0)
    return fig, axes


def header(fig, t, title, subtitle):
    fig.text(0.012, 0.965, title, fontsize=14, fontweight="bold", color=t["ink"], va="top")
    fig.text(0.012, 0.905, subtitle, fontsize=10, color=t["ink2"], va="top")


def footer(fig, t, source):
    fig.text(0.012, 0.018, source, fontsize=8.5, color=t["muted"], va="bottom")


def save(fig, name, mode):
    fig.savefig(OUT / f"{name}-{mode}.png", dpi=180, facecolor=fig.get_facecolor())
    plt.close(fig)


# ── 1. The experiment ledger ─────────────────────────────────────────────────────────────
# docs/BACKTEST_LEDGER.md, main table: 4w mean excess vs SPY, standard 30×9 grid
# (#14/#15: 30×12 Monday grid). Verdicts as recorded there.
LEDGER = [
    ("#4   detection v2 — baseline", -0.54, "baseline"),
    ("#8   + PIT fundamentals v2", 0.53, "accepted"),
    ("#9   + deep analysis v1 (ReAct tools)", 1.78, "accepted"),
    ("#10  detection v3, sector-relative", 0.55, "held"),
    ("#11  deep analysis v2 (planner, parallel)", -0.91, "rejected"),
    ("#12  consensus-in-verdict", 0.11, "rejected"),
    ("#13  #12 + one guard sentence", -0.99, "rejected"),
    ("#14  Monday regime, before PIT audit", 0.89, "void"),
    ("#15  same run, leak-free code", 0.18, "inconclusive"),
]
VERDICT_LABEL = {
    "baseline": "baseline", "accepted": "accepted", "rejected": "rejected",
    "held": "held — no cutover", "void": "void — leak", "inconclusive": "inconclusive",
}


def fig_ledger(mode):
    t = THEMES[mode]
    fig, ax = setup(t, w=9.6, h=5.0)
    fig.subplots_adjust(left=0.36, right=0.77, top=0.80, bottom=0.12)
    names = [r[0] for r in LEDGER][::-1]
    vals = [r[1] for r in LEDGER][::-1]
    verdicts = [r[2] for r in LEDGER][::-1]
    colours = [GOOD if v == "accepted" else CRITICAL if v == "rejected" else t["neutral"]
               for v in verdicts]
    ax.barh(names, vals, color=colours, height=0.62, edgecolor=t["surface"], linewidth=2)
    ax.axvline(0, color=t["axis"], linewidth=1)
    ax.xaxis.grid(True, color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines["left"].set_visible(False)
    ax.set_xlim(-1.4, 2.2)
    ax.set_xlabel("4-week mean excess return vs SPY, %")
    for y, (v, verdict) in enumerate(zip(vals, verdicts)):
        ax.text(2.28, y, f"{v:+.2f}%   {VERDICT_LABEL[verdict]}", va="center",
                fontsize=9.5, color=t["ink2"], transform=ax.transData, clip_on=False)
    header(fig, t, "Every configuration change is its own backtest id",
           "Walk-forward sweeps on one grid (30 tickers × 9 weekly cohorts). One bull regime — "
           "CIs on means include zero;\nverdicts weigh decision-level evidence, not the headline "
           "number. #14 vs #15: the lookahead leak was worth ~0.7pp.")
    footer(fig, t, "Source: docs/BACKTEST_LEDGER.md")
    save(fig, "ledger", mode)


# ── 2. Event study: forward excess by synthesis-matrix cell ──────────────────────────────
# docs/EVENT_STUDY_2026-08-20.md §4: +28d excess vs SPY, 95% cohort-bootstrap CI.
# The two SHORT cells with n<10 are omitted here (CIs not reported); they are in the doc.
CELLS = [
    ("LONG · confirms", 72, 21, 8.95, 4.49, 13.51),
    ("LONG · contradicts", 69, 21, 8.20, 3.71, 14.09),
    ("LONG · ignores", 16, 11, -1.89, -6.70, 3.91),
    ("SHORT · confirms", 87, 23, -1.25, -4.97, 2.59),
]


def fig_cells(mode):
    t = THEMES[mode]
    fig, ax = setup(t, w=9.0, h=4.3)
    fig.subplots_adjust(left=0.22, right=0.97, top=0.76, bottom=0.15)
    ys = list(range(len(CELLS)))[::-1]
    for y, (name, n, coh, mean, lo, hi) in zip(ys, CELLS):
        thin = n < 20
        c = t["muted"] if thin else t["s1"]
        ax.plot([lo, hi], [y, y], color=c, linewidth=2, solid_capstyle="round")
        ax.plot(mean, y, "o", color=c, markersize=9, markeredgecolor=t["surface"],
                markeredgewidth=2)
        note = f"{mean:+.2f}%  (n={n}, {coh} cohorts{', thin' if thin else ''})"
        if name.startswith("SHORT"):
            note += f" — short P&L {-mean:+.2f}%"
        ax.text(hi + 0.5, y, note, va="center", fontsize=9.5, color=t["ink2"])
    ax.set_yticks(ys, [c[0] for c in CELLS])
    ax.axvline(0, color=t["axis"], linewidth=1)
    ax.xaxis.grid(True, color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines["left"].set_visible(False)
    ax.set_xlim(-8, 22)
    ax.set_xlabel("Stock price minus SPY, 28 days after the decision, %  (dot = mean, line = 95% CI)")
    header(fig, t, "Where the decision matrix earns, and where it does not",
           "Price path after each (decision × fundamentals-verdict) cell. LONG·confirms works "
           "whether the stock came in\nrising or falling; the short book is one flat cell. "
           "Exploratory — post-hoc splits on burned windows.")
    footer(fig, t, "Source: docs/EVENT_STUDY_2026-08-20.md §4")
    save(fig, "matrix_cells", mode)


# ── 3. Horizon curve ─────────────────────────────────────────────────────────────────────
# docs/BACKTEST_LEDGER.md "Aux horizons 6w/8w": net beta-adjusted excess, holdout pair, 100%
# of trades matured at every horizon.
HORIZONS = [2, 4, 6, 8, 12]
HOLDOUT_ON = [1.54, 4.95, 5.57, 6.18, 5.36]
HOLDOUT_OFF = [1.23, 2.34, 2.61, 4.12, 1.75]


def fig_horizon(mode):
    t = THEMES[mode]
    fig, ax = setup(t, w=9.0, h=4.3)
    fig.subplots_adjust(left=0.08, right=0.84, top=0.76, bottom=0.15)
    for ys, c, label in ((HOLDOUT_ON, t["s1"], "deep analysis ON (n=79)"),
                         (HOLDOUT_OFF, t["s2"], "deep analysis OFF (n=76)")):
        ax.plot(HORIZONS, ys, color=c, linewidth=2, marker="o", markersize=8,
                markeredgecolor=t["surface"], markeredgewidth=2, label=label)
        ax.text(12.4, ys[-1], label.split(" (")[0].replace("deep analysis ", "deep "),
                va="center", fontsize=9.5, color=t["ink2"])
        i = ys.index(max(ys))
        ax.annotate(f"{ys[i]:+.2f}%", (HORIZONS[i], ys[i]), textcoords="offset points",
                    xytext=(0, 10), ha="center", fontsize=9.5, color=t["ink"])
    ax.set_xticks(HORIZONS, [f"{h}w" for h in HORIZONS])
    ax.set_ylim(0, 7.5)
    ax.yaxis.grid(True, color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_ylabel("net excess vs SPY, %")
    ax.set_xlabel("holding horizon")
    leg = ax.legend(loc="upper left", frameon=False, fontsize=9.5)
    for txt in leg.get_texts():
        txt.set_color(t["ink2"])
    header(fig, t, "The edge does not decay past 4 weeks — it peaks near 8",
           "Same stored decisions graded at five horizons. Diagnostic only, declared before "
           "looking: 4w grading likely\ncuts the signal mid-growth, consistent with the "
           "agent's own stated 6–12 week thesis horizon.")
    footer(fig, t, "Source: docs/BACKTEST_LEDGER.md — \"Aux horizons 6w/8w\" (holdout pair, 100% matured)")
    save(fig, "horizon_curve", mode)


# ── 4. Beta, not selection ───────────────────────────────────────────────────────────────
# docs/EVENT_STUDY_2026-08-20.md §7.2: 4w P&L per leg by position beta.
BETA_BUCKETS = ["β < 0.8", "0.8 – 1.2", "β > 1.2"]
BETA_LONG = [(-0.42, 63), (1.41, 59), (7.78, 107)]
BETA_SHORT = [(0.53, 70), (3.84, 28), (-3.75, 32)]


def fig_beta(mode):
    t = THEMES[mode]
    fig, ax = setup(t, w=9.0, h=4.3)
    fig.subplots_adjust(left=0.08, right=0.97, top=0.76, bottom=0.13)
    x = range(len(BETA_BUCKETS))
    w = 0.36
    for off, data, c, label in ((-w / 2 - 0.01, BETA_LONG, t["s1"], "LONG leg"),
                                (w / 2 + 0.01, BETA_SHORT, t["s2"], "SHORT leg")):
        xs = [i + off for i in x]
        vals = [d[0] for d in data]
        ax.bar(xs, vals, width=w, color=c, label=label, edgecolor=t["surface"], linewidth=2)
        for xi, (v, n) in zip(xs, data):
            ax.text(xi, v + (0.35 if v >= 0 else -0.35), f"{v:+.2f}%\nn={n}", ha="center",
                    va="bottom" if v >= 0 else "top", fontsize=9, color=t["ink2"])
    ax.axhline(0, color=t["axis"], linewidth=1)
    ax.set_xticks(list(x), BETA_BUCKETS)
    ax.set_ylim(-6.5, 10.5)
    ax.yaxis.grid(True, color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_ylabel("4w P&L for the leg, %")
    leg = ax.legend(loc="upper left", frameon=False, fontsize=9.5)
    for txt in leg.get_texts():
        txt.set_color(t["ink2"])
    header(fig, t, "Self-critique: most of the long leg's return is market beta",
           "In a +15.6% SPY window the book earns by being long high-beta and short low-beta "
           "names. 18 April-2026\nsemiconductor longs (6% of trades) average +34%; the other "
           "94% of longs average +0.50%.")
    footer(fig, t, "Source: docs/EVENT_STUDY_2026-08-20.md §7.1–7.2")
    save(fig, "beta", mode)


# ── 5. Significance pre-filter: a negative result ────────────────────────────────────────
# significance/data/phase2_results.csv (PR-AUC, test split) and SIGNIFICANCE_EXPERIMENT.md
# "Результаты Фазы 2": group permutation importance, ΔPR-AUC when the modality is destroyed.
PRAUC = [("regex", 0.082), ("embeddings", 0.108), ("metadata", 0.286), ("hybrid", 0.307)]
BASE_RATE = 0.079
PERM = [("regex", 0.001), ("embeddings", 0.010), ("metadata", 0.224)]


def fig_significance(mode):
    t = THEMES[mode]
    fig, (a1, a2) = setup(t, w=9.6, h=4.3, ncols=2)
    fig.subplots_adjust(left=0.07, right=0.98, top=0.74, bottom=0.14, wspace=0.32)
    names = [p[0] for p in PRAUC]
    vals = [p[1] for p in PRAUC]
    a1.bar(names, vals, color=[t["neutral"], t["neutral"], t["s1"], t["s1"]], width=0.6,
           edgecolor=t["surface"], linewidth=2)
    a1.axhline(BASE_RATE, color=t["muted"], linewidth=1.2, linestyle=(0, (4, 3)))
    a1.text(1.5, BASE_RATE - 0.006, "base\nrate\n7.9%", ha="center", va="top", fontsize=8.5,
            color=t["muted"], linespacing=1.1)
    for i, v in enumerate(vals):
        a1.text(i, v + 0.008, f"{v:.3f}", ha="center", fontsize=9.5, color=t["ink2"])
    a1.set_ylim(0, 0.36)
    a1.set_title("PR-AUC on the time-split test set", fontsize=10.5, loc="left", color=t["ink"])
    a1.yaxis.grid(True, color=t["grid"], linewidth=0.8)
    a1.set_axisbelow(True)

    pn = [p[0] for p in PERM]
    pv = [p[1] for p in PERM]
    a2.barh(pn[::-1], pv[::-1], color=[t["s1"], t["neutral"], t["neutral"]], height=0.55,
            edgecolor=t["surface"], linewidth=2)
    for y, v in enumerate(pv[::-1]):
        a2.text(v + 0.004, y, f"{v:+.3f}", va="center", fontsize=9.5, color=t["ink2"])
    a2.set_xlim(0, 0.27)
    a2.set_title("PR-AUC lost when the modality is shuffled", fontsize=10.5, loc="left",
                 color=t["ink"])
    a2.xaxis.grid(True, color=t["grid"], linewidth=0.8)
    a2.set_axisbelow(True)
    a2.spines["left"].set_visible(False)
    header(fig, t, "A negative result, kept: the classifier learned the clock, not the news",
           "Cheap pre-filter meant to predict which headlines move the price. Text carries "
           "~zero signal; the apparent\nlift is one feature — minutes since the open "
           "(base rate 23.5% at the open vs 1.2% at the close).")
    footer(fig, t, "Source: SIGNIFICANCE_EXPERIMENT.md — Phase 2 ablation")
    save(fig, "significance", mode)


if __name__ == "__main__":
    for m in THEMES:
        fig_ledger(m)
        fig_cells(m)
        fig_horizon(m)
        fig_beta(m)
        fig_significance(m)
    print("written to", OUT)
