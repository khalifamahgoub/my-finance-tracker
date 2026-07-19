"""Financial-month math. The cycle runs 23rd -> 22nd and each period is named by the
month that contains the 22nd (so "Feb 2026" = 23 Jan 2026 .. 22 Feb 2026).

Pure functions only — the single source of truth for every date-bucketing decision in
the pipeline. Unit-tested in tests/test_periods.py.
"""
from __future__ import annotations

from datetime import date, timedelta

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH_NUM = {m.lower(): i + 1 for i, m in enumerate(MONTHS)}

# Day the financial month rolls over. 23rd..end-of-month belong to the NEXT period.
ROLLOVER_DAY = 23


def period_id_of(d: date) -> str:
    """Map a date to its financial period id 'YYYY-MM' (month containing the 22nd)."""
    y, m = d.year, d.month
    if d.day >= ROLLOVER_DAY:
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return f"{y:04d}-{m:02d}"


def salary_period(d: date) -> str:
    """The financial period a monthly paycheck funds, computed as if it were paid on the
    25th of its calendar month. The pay date drifts across the 23rd rollover (some months
    the 18th, others the 25th), which otherwise lands two paychecks in one period and none
    in the next; normalising to the 25th gives exactly one paycheck per period."""
    return period_id_of(d.replace(day=25))


def period_bounds(period_id: str) -> tuple[date, date]:
    """(start, end) inclusive for a period id. Start = 23rd of prior month, end = 22nd."""
    y, m = _split(period_id)
    end = date(y, m, 22)
    py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
    start = date(py, pm, ROLLOVER_DAY)
    return start, end


def period_label(period_id: str) -> str:
    """'2026-02' -> 'Feb 2026'."""
    y, m = _split(period_id)
    return f"{MONTHS[m - 1]} {y}"


def parse_period(name: str) -> str:
    """Accept 'Feb 2026', 'feb 2026', or '2026-02' -> canonical '2026-02'."""
    s = name.strip()
    if "-" in s and s[:4].isdigit():
        return _canonical(*_split(s))
    parts = s.replace(",", " ").split()
    if len(parts) == 2 and parts[0].lower()[:3] in _MONTH_NUM:
        return _canonical(int(parts[1]), _MONTH_NUM[parts[0].lower()[:3]])
    raise ValueError(f"Unrecognised period: {name!r} (use 'Feb 2026' or '2026-02')")


def current_period_id(today: date | None = None) -> str:
    return period_id_of(today or date.today())


def next_period_id(period_id: str) -> str:
    y, m = _split(period_id)
    m += 1
    if m == 13:
        m, y = 1, y + 1
    return _canonical(y, m)


def period_row(period_id: str) -> dict[str, str]:
    """DB row for the periods table."""
    start, end = period_bounds(period_id)
    return {
        "period_id": period_id,
        "label": period_label(period_id),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }


def periods_spanning(start: date, end: date) -> list[str]:
    """All period ids touched by an inclusive date range (e.g. one statement)."""
    ids: list[str] = []
    d = start
    while d <= end:
        pid = period_id_of(d)
        if pid not in ids:
            ids.append(pid)
        d += timedelta(days=1)
    return ids


def _split(period_id: str) -> tuple[int, int]:
    y, m = period_id.split("-")
    return int(y), int(m)


def _canonical(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}"
