#!/usr/bin/env python3
"""Event study around the long-term agent's decisions, and book-composition cuts.

Consolidates the ad-hoc analysis written up in `docs/EVENT_STUDY_2026-08-20.md` so the
numbers there are reproducible with one command instead of re-derivation.

Not a decision path. Every forward price here is a realized outcome, so under
`docs/PIT_RULES.md` §4 this module belongs to grading and may see the future by
construction. The BACKWARD leg is still PIT-honest: day 0 is the last close at or before
`as_of`, so a pre-move split is computable from what the decision itself could see.

TWO SIGN CONVENTIONS, both of which have already caused wrong readings once:

  1. `backtest_runs.excess` / `.excess_beta` are DIRECTION-SIGNED — `stats.trade_excess`
     returns `-(fwd - bench)` for a SHORT. The column is the leg's P&L, not the price move.
     Flipping it again is the error `stats.unrealized_excess`'s docstring warns about.
     Here: `pnl` is the stored value; `price_excess` is `pnl` for LONG and `-pnl` for SHORT.
  2. The pre-move must be the STANDARD trailing excess `(c[0]/c[-21]-1) - (spy…)`, not the
     day-0-normalised path read backwards (which measures `R/(1+R)` and understates rises).

`longterm_agent_results` stores no `excess` column: it keeps raw `fwd_return_*` and
`benchmark_return_*`, so the sign is applied here (`_live_excess`).

Cohort blocking: detections and decisions cluster on the same `as_of` dates, so every CI
resamples COHORTS, never rows.

Usage:
    python3 -m longterm_agent.event_study --cut direction --plot
    python3 -m longterm_agent.event_study --cut cell --plot
    python3 -m longterm_agent.event_study --cut premove --plot
    python3 -m longterm_agent.event_study --cut legs
    python3 -m longterm_agent.event_study --cut variants --plot
    python3 -m longterm_agent.event_study --cut spy --plot
    python3 -m longterm_agent.event_study --cut live
    python3 -m longterm_agent.event_study --cut all --plot --outdir /tmp/es
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.db import connect  # noqa: E402

PRE, POST = 21, 28
MIN_N, MIN_COHORTS = 20, 3
SEMI = {"INTC", "AMD", "MU", "NVDA", "AVGO", "TSM", "QCOM", "TXN", "ADI",
        "AMAT", "LRCX", "KLAC", "MRVL"}
VERDICT_COL = {"confirms": "#1f6feb", "contradicts": "#d29922", "ignores": "#8250df"}


# --------------------------------------------------------------------------- loading
def _sql(conn, q):
    return pd.read_sql(q, conn)


def load_all(horizon=28):
    """Everything the cuts need, in one connection."""
    conn = connect()
    try:
        px = _sql(conn, "SELECT ticker, date, close FROM stock_prices_daily")
        det = _sql(conn, """
            SELECT ticker, as_of, direction, magnitude_z, attention_spike_ratio
            FROM shift_library WHERE direction IS NOT NULL
        """)
        dec = _sql(conn, f"""
            SELECT ticker, as_of, verdict, dir, pnl, pnl_beta, fwd, spy, beta FROM (
                SELECT ticker, as_of,
                       min(gate_trace->'table_inputs'->>'verdict') verdict,
                       min(direction) dir,
                       avg(excess)*100 pnl, avg(excess_beta)*100 pnl_beta,
                       avg(fwd_return)*100 fwd, avg(benchmark_return)*100 spy,
                       avg(beta) beta,
                       count(DISTINCT gate_trace->'table_inputs'->>'verdict') nv,
                       count(DISTINCT direction) nd,
                       bool_or((signal->>'synthesis_failed')::bool) failed
                FROM backtest_runs
                WHERE horizon_days = {int(horizon)} AND traded AND excess IS NOT NULL
                  AND direction IN ('LONG','SHORT') AND gate_trace ? 'table_inputs'
                GROUP BY 1,2
            ) t WHERE nv = 1 AND nd = 1 AND NOT COALESCE(failed, false)
        """)
        legs = _sql(conn, """
            SELECT ticker, as_of, horizon_days h, dir, pnl FROM (
                SELECT ticker, as_of, horizon_days, min(direction) dir,
                       avg(excess)*100 pnl, count(DISTINCT direction) nd
                FROM backtest_runs
                WHERE traded AND excess IS NOT NULL AND direction IN ('LONG','SHORT')
                GROUP BY 1,2,3
            ) t WHERE nd = 1
        """)
        sec = _sql(conn, """
            SELECT DISTINCT ON (ticker) ticker, data->>'sector' sector FROM fundamentals
            WHERE source='yfinance' AND kind='info' AND data->>'sector' IS NOT NULL
            ORDER BY ticker, fetch_date DESC
        """)
        live = _sql(conn, """
            SELECT ticker, as_of, verdict, traded, conviction, beta,
                   fundamentals_summary->>'verdict' fv,
                   signal->>'direction' dir,
                   background_shift->>'direction' shift_dir,
                   fwd_return_2w*100 f2, benchmark_return_2w*100 b2,
                   fwd_return_4w*100 f4, benchmark_return_4w*100 b4,
                   fwd_return_stopped*100 fst, benchmark_return_stopped*100 bst,
                   excess_beta*100 pnl_beta, exit_reason, risk_flags
            FROM longterm_agent_results
        """)
    finally:
        conn.close()

    px["date"] = pd.to_datetime(px["date"])
    for d in (det, dec, legs, live):
        d["as_of"] = pd.to_datetime(d["as_of"])
    dec = dec.merge(sec, on="ticker", how="left")
    dec["sector"] = dec["sector"].fillna("unknown")
    # price_excess undoes the direction sign; `abs_pnl` is the unhedged position return
    dec["price_excess"] = np.where(dec.dir == "LONG", dec.pnl, -dec.pnl)
    dec["abs_pnl"] = np.where(dec.dir == "LONG", dec.fwd, -dec.fwd)
    series = {t: g.sort_values("date").set_index("date")["close"]
              for t, g in px.groupby("ticker")}
    return {"series": series, "det": det, "dec": dec, "legs": legs, "live": live}


# --------------------------------------------------------------------------- helpers
def event_path(series, ticker, as_of):
    """(forward path normalised to 0 at day 0, standard trailing excess into day 0)."""
    s = series.get(ticker)
    spy = series.get("SPY")
    if s is None or spy is None:
        return None, None
    hist = s.loc[:as_of]
    if hist.empty:
        return None, None
    d0 = hist.index[-1]
    try:
        i = s.index.get_loc(d0)
        j = spy.index.get_loc(d0)
    except KeyError:
        return None, None
    if i < PRE or j < PRE or i + POST >= len(s) or j + POST >= len(spy):
        return None, None
    w = s.iloc[i - PRE:i + POST + 1].values
    b = spy.iloc[j - PRE:j + POST + 1].values
    if len(w) != PRE + POST + 1 or len(b) != PRE + POST + 1:
        return None, None
    fwd = 100.0 * (w / w[PRE] - 1) - 100.0 * (b / b[PRE] - 1)
    pre = 100.0 * (w[PRE] / w[0] - 1) - 100.0 * (b[PRE] / b[0] - 1)
    return fwd, pre


def block_ci(values, cohorts, iters=4000, seed=11):
    """95% percentile bootstrap resampling COHORTS. Returns (lo, hi) or (None, None)."""
    v = np.asarray(values, dtype=float)
    g = np.asarray(cohorts)
    ok = ~np.isnan(v)
    v, g = v[ok], g[ok]
    uniq = np.unique(g)
    if len(uniq) < MIN_COHORTS or len(v) == 0:
        return None, None
    rng = np.random.default_rng(seed)
    idx = [np.where(g == c)[0] for c in uniq]
    draws = np.empty(iters)
    for k in range(iters):
        pick = np.concatenate([idx[i] for i in rng.integers(0, len(idx), len(idx))])
        draws[k] = v[pick].mean()
    return float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))


def band(mat, cohorts, iters=2000, seed=11):
    uniq = np.unique(cohorts)
    if len(uniq) < MIN_COHORTS:
        return None, None
    rng = np.random.default_rng(seed)
    idx = [np.where(cohorts == c)[0] for c in uniq]
    draws = np.empty((iters, mat.shape[1]))
    for k in range(iters):
        pick = np.concatenate([idx[i] for i in rng.integers(0, len(idx), len(idx))])
        draws[k] = mat[pick].mean(axis=0)
    return np.percentile(draws, 2.5, axis=0), np.percentile(draws, 97.5, axis=0)


def collect(series, rows, key):
    """{group: (path matrix, cohort array, pre-move array)} from an iterable of namedtuples."""
    out = {}
    for r in rows.itertuples():
        fwd, pre = event_path(series, r.ticker, r.as_of)
        if fwd is None:
            continue
        k = key(r)
        if k is None:
            continue
        out.setdefault(k, ([], [], []))
        out[k][0].append(fwd)
        out[k][1].append(r.as_of)
        out[k][2].append(pre)
    return {k: (np.vstack(v[0]), np.array(v[1]), np.array(v[2]))
            for k, v in out.items() if v[0]}


def hhi(s):
    p = s.value_counts() / len(s)
    return float((p ** 2).sum())


def print_paths(title, groups, order=None):
    print(f"\n### {title}")
    print(f"{'group':<28}{'n':>5}{'coh':>5}{'pre%':>8}{'+10d':>9}{'+28d':>9}{'med':>8}  CI95 at +28d")
    for k in (order or groups):
        if k not in groups:
            continue
        mat, coh, pre = groups[k]
        mean, med = mat.mean(axis=0), np.median(mat, axis=0)
        thin = mat.shape[0] < MIN_N or len(np.unique(coh)) < MIN_COHORTS
        lo, hi = (None, None) if thin else band(mat, coh)
        ci = f"[{lo[-1]:+.2f}, {hi[-1]:+.2f}]" if lo is not None else "— thin, do not read"
        print(f"{str(k):<28}{mat.shape[0]:>5}{len(np.unique(coh)):>5}{pre.mean():>+8.2f}"
              f"{mean[PRE + 10]:>+9.2f}{mean[-1]:>+9.2f}{med[-1]:>+8.2f}  {ci}")


def plot_paths(groups, panels, colors, outfile, suptitle, ylabel=None):
    x = np.arange(-PRE, POST + 1)
    fig, axes = plt.subplots(1, len(panels), figsize=(6.4 * len(panels), 6), sharey=True)
    axes = np.atleast_1d(axes)
    for ax, (title, keys) in zip(axes, panels):
        for k in keys:
            if k not in groups:
                continue
            mat, coh, pre = groups[k]
            n, nc = mat.shape[0], len(np.unique(coh))
            thin = n < MIN_N or nc < MIN_COHORTS
            lo, hi = (None, None) if thin else band(mat, coh)
            c = colors.get(k, "#57606a")
            ax.plot(x, mat.mean(axis=0), color=c, lw=1.2 if thin else 2.1,
                    ls=":" if thin else "-", alpha=0.45 if thin else 1.0,
                    label=f"{k} (n={n}, {nc} coh, pre {pre.mean():+.1f}%)"
                          + (" — thin" if thin else ""))
            ax.plot(x, np.median(mat, axis=0), color=c, lw=1.0, ls="--", alpha=0.7)
            if lo is not None:
                ax.fill_between(x, lo, hi, color=c, alpha=0.12, lw=0)
        ax.axvline(0, color="#333", lw=1.0)
        ax.axhline(0, color="#999", lw=0.8)
        ax.set_title(title, fontsize=10.5)
        ax.set_xlabel("trading days relative to the decision")
        ax.legend(fontsize=7.8, loc="upper left")
        ax.grid(alpha=0.22)
    axes[0].set_ylabel(ylabel or "cumulative return vs SPY, % (0 at the decision)")
    fig.suptitle(suptitle, fontsize=11.5)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(outfile, dpi=140)
    plt.close(fig)
    print(f"  saved {outfile}")


# --------------------------------------------------------------------------- cuts
def cut_direction(D, args):
    """Price path around a detection, by the shift's own sign (shift_library)."""
    g = collect(D["series"], D["det"], lambda r: r.direction)
    print_paths("price path by SHIFT sign — shift_library", g, ["positive", "negative"])
    if args.plot:
        plot_paths(g, [("by shift sign — shift_library", ["positive", "negative"])],
                   {"positive": "#1a7f37", "negative": "#b42318"},
                   os.path.join(args.outdir, "es_direction.png"),
                   "Price path around a detected shift — band = 95% cohort-block CI")


def cut_verdict(D, args):
    """Detected shifts split by the stored fundamentals verdict."""
    g = collect(D["series"], D["dec"], lambda r: r.verdict)
    print_paths("price path by FUNDAMENTALS VERDICT (unsigned)", g,
                ["confirms", "contradicts", "ignores"])
    if args.plot:
        plot_paths(g, [("by fundamentals verdict", ["confirms", "contradicts", "ignores"])],
                   VERDICT_COL, os.path.join(args.outdir, "es_verdict.png"),
                   "Detected shifts by fundamentals verdict — paths are RAW price, not P&L")


def cut_cell(D, args):
    """The matrix cells: verdict x the decision the agent took."""
    g = collect(D["series"], D["dec"], lambda r: f"{r.verdict} · {r.dir}")
    keys = [f"{v} · {d}" for d in ("LONG", "SHORT") for v in VERDICT_COL]
    print_paths("price path by MATRIX CELL (verdict × side)", g, keys)
    print("\n  reminder: paths are RAW price. A rising SHORT cell is a LOSS.")
    if args.plot:
        colors = {f"{v} · {d}": VERDICT_COL[v] for v in VERDICT_COL for d in ("LONG", "SHORT")}
        plot_paths(g, [("LONG decisions", [f"{v} · LONG" for v in VERDICT_COL]),
                       ("SHORT decisions — FALLING = the short won",
                        [f"{v} · SHORT" for v in VERDICT_COL])],
                   colors, os.path.join(args.outdir, "es_cell.png"),
                   "Matrix cells — verdict × the decision taken")


def cut_premove(D, args):
    """Decision side x verdict x the sign of the PIT-clean 21-session pre-move."""
    rows = []
    for r in D["dec"].itertuples():
        fwd, pre = event_path(D["series"], r.ticker, r.as_of)
        if fwd is None:
            continue
        rows.append((r.ticker, r.as_of, r.dir, r.verdict,
                     "grew before" if pre > 0 else "fell before"))
    tagged = pd.DataFrame(rows, columns=["ticker", "as_of", "dir", "verdict", "pre"])
    g = collect(D["series"], tagged, lambda r: f"{r.pre} · {r.dir} · {r.verdict}")
    keys = [f"{p} · {d} · {v}" for p in ("grew before", "fell before")
            for d in ("LONG", "SHORT", "NEUTRAL") for v in VERDICT_COL]
    print_paths("price path by PRE-MOVE × side × verdict", g, keys)
    if args.plot:
        colors = {k: VERDICT_COL[k.split(" · ")[2]] for k in g}
        panels = [(f"{d} · {p}", [f"{p} · {d} · {v}" for v in VERDICT_COL])
                  for p in ("grew before", "fell before") for d in ("LONG", "SHORT")]
        plot_paths(g, panels[:4], colors, os.path.join(args.outdir, "es_premove.png"),
                   "Forward path split by the 21-day pre-move (standard trailing excess)")


def cut_legs(D, args):
    """LONG vs SHORT across every swept book, all horizons. `pnl` is each leg's P&L."""
    legs = D["legs"]
    print("\n### LONG vs SHORT — deduped to unique (ticker, as_of), `excess` is leg P&L")
    print(f"{'h':>4}{'leg':>7}{'n':>6}{'P&L':>9}{'median':>9}{'CI95':>22}{'price vs SPY':>14}")
    for h in sorted(legs.h.dropna().unique()):
        g = legs[legs.h == h]
        if len(g) < 20:
            continue
        tot = {}
        for d in ("LONG", "SHORT"):
            s = g[g.dir == d]
            if len(s) < 5:
                continue
            lo, hi = block_ci(s.pnl, s.as_of)
            tot[d] = s.pnl.mean()
            price = s.pnl.mean() if d == "LONG" else -s.pnl.mean()
            ci = f"[{lo:+.2f}, {hi:+.2f}]" if lo is not None else "—"
            print(f"{int(h):>4}{d:>7}{len(s):>6}{s.pnl.mean():>+9.2f}{s.pnl.median():>+9.2f}"
                  f"{ci:>22}{price:>+14.2f}")
        if len(tot) == 2:
            print(f"{'':>4}{'spread':>7}  longed names − shorted names, in price: "
                  f"{tot['LONG'] + tot['SHORT']:+.2f}pp")


def _variants(dec):
    return {"A · current (L+S, all)": dec,
            "D · confirms only (L+S)": dec[dec.verdict == "confirms"],
            "C · LONG · confirms": dec[(dec.verdict == "confirms") & (dec.dir == "LONG")]}


def cut_variants(D, args):
    """Book composition under the `confirms` filters."""
    dec = D["dec"]
    V = _variants(dec)
    dates = sorted(dec.as_of.unique())
    print("\n### Book variants — composition")
    print(f"{'variant':<26}{'n':>5}{'L':>5}{'S':>5}{'L:S':>10}{'names':>7}{'sect':>5}"
          f"{'HHItk':>7}{'HHIsec':>8}{'/date':>7}{'beta':>6}{'P&L':>8}{'med':>7}")
    for k, g in V.items():
        L, S = int((g.dir == "LONG").sum()), int((g.dir == "SHORT").sum())
        ratio = f"{L / S:.2f}" if S else ("long-only" if L else "—")
        print(f"{k:<26}{len(g):>5}{L:>5}{S:>5}{ratio:>10}{g.ticker.nunique():>7}"
              f"{g.sector.nunique():>5}{hhi(g.ticker):>7.3f}{hhi(g.sector):>8.3f}"
              f"{len(g) / g.as_of.nunique():>7.1f}{g.beta.mean():>6.2f}"
              f"{g.pnl.mean():>+8.2f}{g.pnl.median():>+7.2f}")
    print("\n### CI, tail dependence, cohort coverage, and where the return comes from")
    for k, g in V.items():
        lo, hi = block_ci(g.pnl, g.as_of)
        s = g.pnl.sort_values()
        cnt = g.groupby("as_of").size().reindex(dates).fillna(0)
        m = g.ticker.isin(SEMI) & (g.as_of.astype(str).str[:7] == "2026-04")
        print(f"  {k}")
        print(f"     CI [{lo:+.2f}, {hi:+.2f}] | top5 = {100 * s[-5:].sum() / s.sum():.1f}% of P&L"
              f" | active {int((cnt > 0).sum())}/{len(dates)} dates, {int((cnt < 3).sum())} below 3")
        print(f"     Apr-2026 semis {int(m.sum())} ({100 * m.mean():.1f}%) | ex-semis mean "
              f"{g[~m].pnl.mean():+.2f}% | pre-May {g[g.as_of < '2026-05-01'].pnl.mean():+.2f}% "
              f"| post-May {g[g.as_of >= '2026-05-01'].pnl.mean():+.2f}%")
    if args.plot:
        _plot_variants(V, dates, os.path.join(args.outdir, "es_variants.png"), dec)


def _plot_variants(V, dates, outfile, dec):
    import matplotlib.gridspec as gridspec
    colors = ["#6e7781", "#1f6feb", "#1a7f37"]
    fig = plt.figure(figsize=(17, 8.5))
    gs = gridspec.GridSpec(len(V), 2, width_ratios=[1, 1.35], hspace=0.42, wspace=0.22)
    ax = fig.add_subplot(gs[:, 0])
    order = dec.sector.value_counts().index.tolist()
    y = np.arange(len(order))
    h = 0.8 / len(V)
    for i, ((k, g), c) in enumerate(zip(V.items(), colors)):
        share = g.sector.value_counts(normalize=True).reindex(order).fillna(0) * 100
        ax.barh(y + (len(V) / 2 - i - 0.5) * h, share, height=h, color=c, alpha=0.9,
                label=f"{k}  (n={len(g)})")
    ax.set_yticks(y); ax.set_yticklabels(order, fontsize=9); ax.invert_yaxis()
    ax.set_xlabel("share of the book's trades, %"); ax.legend(fontsize=8.5)
    ax.grid(alpha=0.22, axis="x"); ax.set_title("(a) sector mix", fontsize=10.5)
    ymax = max(1, dec.groupby("as_of").size().max() * 1.1)
    for i, ((k, g), c) in enumerate(zip(V.items(), colors)):
        a = fig.add_subplot(gs[i, 1])
        cnt = g.groupby("as_of").size().reindex(dates).fillna(0)
        a.bar(cnt.index, cnt.values, width=4, color=c, alpha=0.9)
        a.axhline(3, color="#b42318", lw=1, ls="--", alpha=0.7)
        a.set_ylabel("trades", fontsize=9); a.set_ylim(0, ymax)
        a.set_title(f"{k} — {int((cnt > 0).sum())}/{len(dates)} dates active, "
                    f"{int((cnt < 3).sum())} below 3", fontsize=9.5)
        a.grid(alpha=0.22); a.tick_params(labelsize=8)
        if i < len(V) - 1:
            a.set_xticklabels([])
        else:
            a.set_xlabel("cohort date"); a.tick_params(axis="x", rotation=45, labelsize=8)
    fig.suptitle("What the `confirms` filter does to the book — sector mix and cohort coverage",
                 fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.95]); fig.savefig(outfile, dpi=140); plt.close(fig)
    print(f"  saved {outfile}")


def cut_spy(D, args):
    """Market dependence of each variant, in both frames."""
    V = _variants(D["dec"])
    print("\n### Market dependence — one point per cohort date")
    print(f"{'variant':<26}{'frame':<9}{'slope':>8}{'R2':>7}{'SPY<0':>9}{'SPY>=0':>9}")
    fits = {}
    for k, g in V.items():
        for frame, col in (("excess", "pnl"), ("unhedged", "abs_pnl")):
            q = g.groupby("as_of").agg(y=(col, "mean"), x=("spy", "mean")).reset_index()
            slope, inter = np.polyfit(q.x, q.y, 1)
            r2 = float(np.corrcoef(q.x, q.y)[0, 1] ** 2)
            fits[(k, frame)] = (q, slope, inter, r2)
            print(f"{k:<26}{frame:<9}{slope:>+8.2f}{r2:>7.2f}"
                  f"{g[g.spy < 0][col].mean():>+9.2f}{g[g.spy >= 0][col].mean():>+9.2f}")
    for d in ("LONG", "SHORT"):
        g = D["dec"][D["dec"].dir == d]
        q = g.groupby("as_of").agg(y=("abs_pnl", "mean"), x=("spy", "mean")).reset_index()
        slope, _ = np.polyfit(q.x, q.y, 1)
        r2 = float(np.corrcoef(q.x, q.y)[0, 1] ** 2)
        print(f"{d + ' leg':<26}{'unhedged':<9}{slope:>+8.2f}{r2:>7.2f}"
              f"{g[g.spy < 0].abs_pnl.mean():>+9.2f}{g[g.spy >= 0].abs_pnl.mean():>+9.2f}")
    if args.plot:
        colors = ["#6e7781", "#1f6feb", "#1a7f37"]
        fig, axes = plt.subplots(1, 2, figsize=(13, 6))
        for a, frame, title in ((axes[0], "excess", "(a) EXCESS vs SPY — market netted out"),
                                (axes[1], "unhedged", "(b) UNHEDGED P&L — what the account earns")):
            for (k, _), c in zip(V.items(), colors):
                q, slope, inter, r2 = fits[(k, frame)]
                a.scatter(q.x, q.y, s=26, color=c, alpha=0.65, edgecolors="w", linewidths=0.6,
                          label=f"{k}: slope {slope:+.2f}, R²={r2:.2f}")
                xs = np.linspace(q.x.min(), q.x.max(), 10)
                a.plot(xs, inter + slope * xs, color=c, lw=1.6, ls="--", alpha=0.85)
            a.axhline(0, color="#999", lw=0.8); a.axvline(0, color="#333", lw=1.1)
            a.set_xlabel("SPY over the same window, %"); a.set_ylabel("cohort mean, %")
            a.set_title(title, fontsize=10); a.legend(fontsize=8.5); a.grid(alpha=0.22)
        fig.suptitle("Book behaviour against SPY's own move", fontsize=12)
        fig.tight_layout(rect=[0, 0, 1, 0.93])
        out = os.path.join(args.outdir, "es_spy.png")
        fig.savefig(out, dpi=140); plt.close(fig); print(f"  saved {out}")


def _live_excess(row, fwd_col, bench_col):
    """`longterm_agent_results` stores raw returns; apply the trade_excess sign here."""
    f, b = row[fwd_col], row[bench_col]
    if pd.isna(f) or pd.isna(b):
        return np.nan
    raw = f - b
    return raw if row["dir"] == "LONG" else -raw


def cut_live(D, args):
    """The live book, and what the `confirms` filters would have done to it."""
    live = D["live"].copy()
    print(f"\n### Live runs — {len(live)} on {live.as_of.nunique()} dates, "
          f"{live.ticker.nunique()} tickers, {live.as_of.min().date()} → {live.as_of.max().date()}")
    print(live.verdict.value_counts().to_string())
    t = live[live.traded].copy()
    for lbl, f, b in (("2w", "f2", "b2"), ("4w", "f4", "b4"), ("stopped", "fst", "bst")):
        t[f"ex_{lbl}"] = t.apply(lambda r: _live_excess(r, f, b), axis=1)
    print(f"\ntraded {len(t)} | matured: 2w {int(t.ex_2w.notna().sum())}, "
          f"4w {int(t.ex_4w.notna().sum())}, stopped-book {int(t.ex_stopped.notna().sum())}")
    print("\n### verdict × direction across the traded rows")
    print(pd.crosstab(t.fv.fillna("(missing)"), t.dir).to_string())

    V = {"live book (as traded)": t,
         "confirms_only (registered)": t[t.fv == "confirms"],
         "LONG · confirms only": t[(t.fv == "confirms") & (t.dir == "LONG")]}
    print("\n### the live book vs the counterfactual filters")
    for k, g in V.items():
        L, S = int((g.dir == "LONG").sum()), int((g.dir == "SHORT").sum())
        print(f"\n  {k}: {len(g)}/{len(t)} kept ({100 * len(g) / len(t):.0f}%), "
              f"{g.ticker.nunique()} names, {g.as_of.nunique()} dates, {L}L/{S}S")
        for lbl in ("2w", "4w", "stopped"):
            gg = g.dropna(subset=[f"ex_{lbl}"])
            if len(gg) == 0:
                print(f"     {lbl:<8} n= 0 — nothing matured")
                continue
            lo, hi = block_ci(gg[f"ex_{lbl}"], gg.as_of)
            ci = f"  CI [{lo:+.2f}, {hi:+.2f}]" if lo is not None else "  (too few cohorts)"
            print(f"     {lbl:<8} n={len(gg):>2}  mean {gg[f'ex_{lbl}'].mean():+6.2f}%  "
                  f"median {gg[f'ex_{lbl}'].median():+6.2f}%  "
                  f"wins {int((gg[f'ex_{lbl}'] > 0).sum())}/{len(gg)}{ci}")

    print("\n### every matured 4w live trade, and what each filter does to it")
    m = t.dropna(subset=["ex_4w"]).sort_values("ex_4w", ascending=False)
    m = m.assign(keep_confirms=m.fv == "confirms",
                 keep_Lconfirms=(m.fv == "confirms") & (m.dir == "LONG"))
    print(m[["ticker", "as_of", "dir", "fv", "conviction", "ex_4w", "pnl_beta",
             "exit_reason", "keep_confirms", "keep_Lconfirms"]]
          .to_string(index=False, float_format=lambda x: f"{x:+.2f}"))

    print("\n### trades the registered rule would have removed (all 34, matured or not)")
    rm = t[t.fv != "confirms"]
    print(rm[["ticker", "as_of", "dir", "fv", "conviction", "ex_2w", "ex_4w"]]
          .to_string(index=False, float_format=lambda x: f"{x:+.2f}"))


CUTS = {"direction": cut_direction, "verdict": cut_verdict, "cell": cut_cell,
        "premove": cut_premove, "legs": cut_legs, "variants": cut_variants,
        "spy": cut_spy, "live": cut_live}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cut", default="all", choices=list(CUTS) + ["all"])
    ap.add_argument("--horizon", type=int, default=28, help="grading horizon in days")
    ap.add_argument("--plot", action="store_true")
    ap.add_argument("--outdir", default=".")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    D = load_all(args.horizon)
    for name in (CUTS if args.cut == "all" else [args.cut]):
        CUTS[name](D, args)


if __name__ == "__main__":
    main()
