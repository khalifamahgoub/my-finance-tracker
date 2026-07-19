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


@pytest.mark.parametrize("pay_date,funds", [
    (date(2026, 2, 25), "2026-03"),   # paid 25th -> next period (already correct)
    (date(2026, 3, 18), "2026-04"),   # paid early (18th) -> still funds Apr, not Mar
    (date(2026, 4, 22), "2026-05"),   # paid 22nd (< rollover) -> normalised forward
    (date(2026, 5, 24), "2026-06"),
    (date(2026, 6, 22), "2026-07"),
    (date(2026, 2, 28), "2026-03"),   # any day in a month maps to one canonical period
])
def test_salary_period_is_one_per_calendar_month(pay_date, funds):
    assert p.salary_period(pay_date) == funds


def test_salary_period_gives_distinct_consecutive_periods():
    # the exact drift that broke income: 18th one month, 24th the next
    got = [p.salary_period(d) for d in
           (date(2026, 3, 18), date(2026, 4, 22), date(2026, 5, 24))]
    assert got == ["2026-04", "2026-05", "2026-06"]      # one each, no double, no gap


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
