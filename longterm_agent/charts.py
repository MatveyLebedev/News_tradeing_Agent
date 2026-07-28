"""PNG charts for the Telegram bot.

Telegram renders no tables and no SVG, so a chart has to arrive as an image and be
readable at phone width in one glance. Two consequences shape everything here:

  * **No tooltips.** A static PNG has no hover layer, so every bar carries its value as a
    direct label at the bar end. That is the one place the usual "never label every point"
    rule inverts — with no interaction, the label IS the axis.
  * **Charts are conditional.** A one-bar bar chart is a stat tile pretending to be a
    chart, and six weeks of trading does not make a distribution. Each builder returns
    None below its minimum, and the caller falls back to a text line. Refusing to draw is
    a feature: a chart implies a population, and implying one we do not have is the same
    mistake this project keeps finding in its own numbers.

Colours are the reference palette's diverging pair (blue ↔ red) on its dark surface, used
unchanged in their documented role. Positive is blue, not green: the red/green pair that
finance conventionally uses is the worst possible choice for colour-blind readers, and
every bar is signed and labelled anyway, so hue never carries the meaning alone.
"""
from io import BytesIO
from typing import Any, Dict, List, Optional

import matplotlib
matplotlib.use("Agg")  # headless container — must precede pyplot
import matplotlib.pyplot as plt  # noqa: E402

# --- reference palette, dark surface (references/palette.md) ---------------------------
SURFACE = "#1a1a19"
TEXT_PRIMARY = "#ffffff"
TEXT_SECONDARY = "#c3c2b7"
GRID = "#383835"        # diverging neutral midpoint, doubles as the hairline grid
POSITIVE = "#3987e5"    # diverging pole — blue
NEGATIVE = "#e66767"    # diverging pole — red

MIN_BARS = 2            # below this a bar chart is a stat tile (see anti-patterns)
MIN_DISTRIBUTION = 3    # below this a spread of outcomes is not a distribution


def _canvas(n_rows: int):
    """Dark figure sized to the row count, with the axis band included in the height."""
    height = 1.15 + 0.42 * n_rows
    fig, ax = plt.subplots(figsize=(7.2, height), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.spines["bottom"].set_linewidth(0.8)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=9, length=0)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8, linestyle="-")  # solid hairline, never dashed
    ax.set_axisbelow(True)
    return fig, ax


def _render(fig) -> bytes:
    buf = BytesIO()
    fig.savefig(buf, format="png", facecolor=SURFACE, bbox_inches="tight", pad_inches=0.28)
    plt.close(fig)
    return buf.getvalue()


def _bars(ax, labels, values):
    """Horizontal diverging bars, values labelled OUTSIDE the bar end so nothing clips."""
    y = range(len(values))
    ax.barh(list(y), values, height=0.52,
            color=[POSITIVE if v >= 0 else NEGATIVE for v in values])
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, color=TEXT_PRIMARY, fontsize=10)
    ax.invert_yaxis()
    ax.axvline(0, color=TEXT_SECONDARY, linewidth=0.9)

    span = max((abs(v) for v in values), default=0.01) or 0.01
    pad = span * 0.06
    for i, v in enumerate(values):
        ax.text(v + (pad if v >= 0 else -pad), i, f"{v * 100:+.1f}%",
                va="center", ha="left" if v >= 0 else "right",
                color=TEXT_PRIMARY, fontsize=9.5)
    ax.set_xlim(-span * 1.45, span * 1.45)
    ax.set_xticks([])


def _title(ax, title, subtitle=None):
    ax.set_title(title, color=TEXT_PRIMARY, fontsize=11.5, loc="left",
                 pad=22 if subtitle else 12)
    if subtitle:
        ax.text(0, 1.02, subtitle, transform=ax.transAxes, ha="left", va="bottom",
                color=TEXT_SECONDARY, fontsize=9)


def _per_row_note(notes):
    """A note repeated identically on every row is noise — hoist it to the subtitle.

    Six bars each stamped "day 2 / 28" says one thing six times and collides with the
    zero line. Returns (row_suffixes, subtitle): exactly one of them carries the text.
    """
    present = [n for n in notes if n]
    if present and len(set(present)) == 1 and len(present) == len(notes):
        return [""] * len(notes), present[0]
    return notes, None


def open_positions_chart(rows: List[Dict[str, Any]], horizon_days: int = 28) -> Optional[bytes]:
    """Current return per open position. None if there is nothing worth drawing."""
    rows = [r for r in rows if r.get("position_return") is not None]
    if len(rows) < MIN_BARS:
        return None
    rows = sorted(rows, key=lambda r: r["position_return"], reverse=True)
    values = [float(r["position_return"]) for r in rows]
    notes, shared = _per_row_note(
        [f"day {r.get('days_open', '?')} / {horizon_days}" for r in rows])
    labels = [f"{r['ticker']}  {r['verdict']}" + (f"   {n}" if n else "")
              for r, n in zip(rows, notes)]
    # The bars answer "which position", the subtitle answers "and how is the book doing" —
    # without it the reader has to eyeball-average seven diverging bars, which is exactly
    # how an unrealized figure gets misread.
    from .stats import book_totals, format_book_totals
    subtitle = " · ".join(x for x in (shared, format_book_totals(book_totals(rows))) if x)

    fig, ax = _canvas(len(rows))
    _bars(ax, labels, values)
    _title(ax, "Open positions — return since entry", subtitle or None)
    return _render(fig)


def closed_trades_chart(rows: List[Dict[str, Any]]) -> Optional[bytes]:
    """Realised excess per closed trade. None below MIN_DISTRIBUTION — see module docstring."""
    rows = [r for r in rows if r.get("excess") is not None]
    if len(rows) < MIN_DISTRIBUTION:
        return None
    rows = sorted(rows, key=lambda r: r["excess"], reverse=True)
    values = [float(r["excess"]) for r in rows]
    notes, subtitle = _per_row_note(
        [(r.get("exit_reason") or "") if r.get("exit_reason") != "horizon" else ""
         for r in rows])
    labels = [f"{r['ticker']}  {r['verdict']}" + (f"   {n}" if n else "")
              for r, n in zip(rows, notes)]

    fig, ax = _canvas(len(rows))
    _bars(ax, labels, values)
    _title(ax, "Closed trades — excess vs SPY", subtitle)
    return _render(fig)
