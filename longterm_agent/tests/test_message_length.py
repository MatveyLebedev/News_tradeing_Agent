"""
Regression guards for the 2026-08-02 silent-/open incident.

What happened: the live book grew to 17 open positions, `format_open_positions_html`
rendered 6673 characters, and Telegram rejects anything over 4096. The BadRequest killed
`cmd_open` before the chart ever sent, and because no error handler was registered
python-telegram-bot only logged "No error handlers are registered" — so the command
answered with nothing at all for two days and read as "the bot is down".

Message length is a property of the data, not of the command: /week, /signals and /errors
all grow the same way. So the guard is on the splitter every handler now goes through,
not on /open alone.
"""
import pytest

from longterm_agent.stats_bot import TELEGRAM_MSG_LIMIT, _split_html


def test_short_text_is_not_split():
    """One message must stay one message — no gratuitous (1/2) on a normal reply."""
    assert _split_html("hello\nworld") == ["hello\nworld"]


def test_every_part_fits_telegram_limit():
    text = "\n".join(f"<b>LINE {i}</b> some padding text here" for i in range(400))
    parts = _split_html(text)
    assert len(parts) > 1
    assert all(len(p) <= TELEGRAM_MSG_LIMIT for p in parts)


def test_split_preserves_content_and_line_boundaries():
    """Reassembly must be lossless, and cuts must land between lines, never inside one."""
    lines = [f"<b>T{i}</b> pos <code>+1.2%</code> vs SPY <code>-0.3%</code>" for i in range(300)]
    parts = _split_html("\n".join(lines))
    assert "\n".join(parts).split("\n") == lines
    for p in parts:
        for line in p.split("\n"):
            assert line in lines


def test_oversized_single_line_is_still_bounded():
    """A line longer than the cap has no safe cut; it must still be chunked, not dropped.

    This is the one case that can tear an HTML tag — _reply_long's plain-text retry is
    the net for it. What must never happen is an over-limit part reaching Telegram.
    """
    parts = _split_html("x" * 10000)
    assert all(len(p) <= TELEGRAM_MSG_LIMIT for p in parts)
    assert "".join(parts) == "x" * 10000


def test_real_open_positions_book_is_chunked():
    """The exact shape that broke: a large book through the real formatter."""
    stats = pytest.importorskip("longterm_agent.stats")
    rows = [{
        "ticker": f"TICK{i}", "verdict": "LONG", "days_open": 12,
        "position_return": 0.031, "excess_now": -0.004,
        "entry_px_used": 100.0 + i, "last_px": 103.0 + i,
        "as_of": "2026-07-05", "matures": "2026-08-02",
        "thesis": "A" * 180, "live": True,
    } for i in range(40)]
    msg = stats.format_open_positions_html(rows)
    assert len(msg) > TELEGRAM_MSG_LIMIT, "fixture too small to exercise the bug"
    assert all(len(p) <= TELEGRAM_MSG_LIMIT for p in _split_html(msg))
