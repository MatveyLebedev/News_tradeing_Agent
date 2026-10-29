"""NYSE session calendar from the exchange's published rules — pure, no database.

Why the graders need a calendar at all (2026-10-29): the exit price is "the close on/before
entry + horizon", and a missing bar on that day is indistinguishable, from the price table
alone, from a day the market was shut. The old guard only asked whether the series reached
past the target, so when the exit day's bar was MISSING and a later one existed, the trade
was priced at the PREVIOUS session's close and the grade was never revisited (the
2026-09-07 cohort's 2w: GS, TMUS, PANW, LLY, CVX, BRK-B, whose 09-22 bar landed on 09-25).
The calendar is what names the exit session, so its absence can be told apart from a
holiday.

Rules (NYSE Rule 7.2): a holiday on Saturday closes the preceding Friday and one on Sunday
closes the following Monday — except New Year's Day on a Saturday, which closes nothing
(the Friday is the year-end accounting day). Unscheduled closures follow no rule and are
listed by hand; a missing entry there only delays grading (the row waits for a bar that
never comes, and shows up in the pending count), it never misprices.
"""
from datetime import date, timedelta
from functools import lru_cache

# Full-day closures no rule produces: Hurricane Sandy and national days of mourning.
UNSCHEDULED_CLOSURES = frozenset({
    date(2012, 10, 29), date(2012, 10, 30),   # Hurricane Sandy
    date(2018, 12, 5),                        # President G. H. W. Bush
    date(2025, 1, 9),                         # President Carter
})

MON, THU, FRI, SAT, SUN = 0, 3, 4, 5, 6


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (Meeus/Jones/Butcher)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + month // 12, month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    return d - timedelta(days=1) if d.weekday() == SAT else (
        d + timedelta(days=1) if d.weekday() == SUN else d)


@lru_cache(maxsize=None)
def nyse_holidays(year: int) -> frozenset:
    """The weekday dates in `year` on which the NYSE is closed for a scheduled holiday."""
    out = {
        _nth_weekday(year, 1, MON, 3),        # Martin Luther King Jr. Day
        _nth_weekday(year, 2, MON, 3),        # Washington's Birthday
        _easter(year) - timedelta(days=2),    # Good Friday
        _last_weekday(year, 5, MON),          # Memorial Day
        _observed(date(year, 7, 4)),          # Independence Day
        _nth_weekday(year, 9, MON, 1),        # Labor Day
        _nth_weekday(year, 11, THU, 4),       # Thanksgiving
        _observed(date(year, 12, 25)),        # Christmas
    }
    new_year = date(year, 1, 1)
    if new_year.weekday() != SAT:             # Saturday: Friday Dec 31 stays open
        out.add(_observed(new_year))
    if year >= 2022:
        out.add(_observed(date(year, 6, 19)))  # Juneteenth
    return frozenset(out)


def is_session(day: date) -> bool:
    """Did (or will) the NYSE hold a regular session on `day`?"""
    return (day.weekday() < SAT and day not in nyse_holidays(day.year)
            and day not in UNSCHEDULED_CLOSURES)


def last_session_on_or_before(day: date) -> date:
    """The session whose close an exit targeting `day` is priced at."""
    while not is_session(day):
        day -= timedelta(days=1)
    return day


def sessions_between(start: date, end: date) -> list:
    """Every session in [start, end], ascending."""
    out, d = [], start
    while d <= end:
        if is_session(d):
            out.append(d)
        d += timedelta(days=1)
    return out
