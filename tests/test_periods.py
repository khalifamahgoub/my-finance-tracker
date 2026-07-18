from datetime import date

import pytest

from finance import periods as p


@pytest.mark.parametrize("d,expected", [
    (date(2026, 1, 23), "2026-02"),   # rollover day -> next period
    (date(2026, 2, 22), "2026-02"),   # last day of Feb period
    (date(2026, 2, 23), "2026-03"),   # first day of Mar period
    (date(2026, 1, 22), "2026-01"),
    (date(2026, 2, 1), "2026-02"),
    (date(2026, 2, 25), "2026-03"),   # salary lands ~25th -> next period
    (date(2026, 12, 23), "2027-01"),  # year boundary
    (date(2026, 12, 22), "2026-12"),
])
def test_period_id_of(d, expected):
    assert p.period_id_of(d) == expected


def test_period_bounds():
    assert p.period_bounds("2026-02") == (date(2026, 1, 23), date(2026, 2, 22))
    assert p.period_bounds("2026-01") == (date(2025, 12, 23), date(2026, 1, 22))
    assert p.period_bounds("2027-01") == (date(2026, 12, 23), date(2027, 1, 22))


def test_period_label():
    assert p.period_label("2026-02") == "Feb 2026"
    assert p.period_label("2026-12") == "Dec 2026"


@pytest.mark.parametrize("name", ["Feb 2026", "feb 2026", "February 2026", "2026-02"])
def test_parse_period(name):
    assert p.parse_period(name) == "2026-02"


def test_parse_period_bad():
    with pytest.raises(ValueError):
        p.parse_period("not a period")


def test_next_period_id():
    assert p.next_period_id("2026-12") == "2027-01"
    assert p.next_period_id("2026-02") == "2026-03"


def test_periods_spanning_straddles_two():
    # A calendar-month Feb statement touches both the Feb and Mar financial periods.
    assert p.periods_spanning(date(2026, 2, 1), date(2026, 2, 28)) == ["2026-02", "2026-03"]


def test_period_row():
    row = p.period_row("2026-02")
    assert row == {
        "period_id": "2026-02",
        "label": "Feb 2026",
        "start_date": "2026-01-23",
        "end_date": "2026-02-22",
    }
