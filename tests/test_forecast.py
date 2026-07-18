import sqlite3
from datetime import date

import pytest

from finance import forecast as fc
from finance import variance as var
from finance.config import Config, SCHEMA_SQL


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    conn.execute("PRAGMA foreign_keys = OFF")   # unit test: computation, not referential integrity
    conn.executemany(
        "INSERT INTO categories(name,section,kind,plan_category) VALUES(?,?,?,?)",
        [("Groceries", "VARIABLE", "expense", "Groceries"),
         ("Salary", "INCOME", "income", "Salary"),
         ("Subscriptions", "VARIABLE", "expense", None)])
    return conn


def _txn(conn, **kw):
    kw.setdefault("currency", "BHD")
    kw.setdefault("is_internal", 0)
    cols = "dedup_key,source_account,txn_date,amount,currency,raw_desc,norm_desc,category,period_id,is_internal,balance"
    conn.execute(
        f"INSERT INTO transactions({cols}) VALUES(:dedup_key,:source_account,:txn_date,:amount,"
        ":currency,:raw_desc,:norm_desc,:category,:period_id,:is_internal,:balance)",
        {"dedup_key": kw["norm_desc"] + kw["txn_date"] + str(kw["amount"]),
         "source_account": kw.get("source_account", "ila_cc"), "txn_date": kw["txn_date"],
         "amount": kw["amount"], "currency": "BHD", "raw_desc": kw["norm_desc"],
         "norm_desc": kw["norm_desc"], "category": kw["category"], "period_id": kw["period_id"],
         "is_internal": kw["is_internal"], "balance": kw.get("balance")})


def test_plan_net():
    conn = _db()
    conn.executemany(
        "INSERT INTO plan_lines(period_id,category,section,planned_amount) VALUES(?,?,?,?)",
        [("2026-04", "Salary", "INCOME", 3770), ("2026-04", "Groceries", "FIXED EXPENSES", 700)])
    n = fc.plan_net(conn, "2026-04")
    assert n == {"income": 3770.0, "expense": 700.0, "net": 3070.0}


def test_pacing_in_progress():
    conn = _db()
    conn.execute("INSERT INTO plan_lines(period_id,category,section,planned_amount) VALUES('2026-02','Groceries','FIXED EXPENSES',700)")
    _txn(conn, norm_desc="A", amount=-100, category="Groceries", period_id="2026-02", txn_date="2026-01-30")
    _txn(conn, norm_desc="B", amount=-50, category="Groceries", period_id="2026-02", txn_date="2026-02-05")
    p = fc.pacing(conn, Config(), "2026-02", today=date(2026, 2, 7))
    assert p["closed"] is False
    assert p["spend_to_date"] == 150.0
    assert p["days"] == 31 and p["day"] == 16          # 23 Jan..22 Feb, as of 7 Feb
    assert 289 < p["projected_spend"] < 292            # 150 / (16/31)
    assert p["on_track"] is True


def test_pacing_closed_no_extrapolation():
    conn = _db()
    _txn(conn, norm_desc="A", amount=-100, category="Groceries", period_id="2026-02", txn_date="2026-02-10")
    p = fc.pacing(conn, Config(), "2026-02", today=date(2026, 7, 1))
    assert p["closed"] is True
    assert p["projected_spend"] == p["spend_to_date"] == 100.0


def test_subscription_changes():
    conn = _db()
    for pid, m, amt, d in [("2026-01", "NETFLIX COM", -5.0, "2026-01-10"),
                           ("2026-01", "DEADSUB", -3.0, "2026-01-11"),
                           ("2026-02", "NETFLIX COM", -5.95, "2026-02-10"),
                           ("2026-02", "SPOTIFY", -4.0, "2026-02-11")]:
        _txn(conn, norm_desc=m, amount=amt, category="Subscriptions", period_id=pid, txn_date=d)
    ch = {c["merchant"]: c for c in var.subscription_changes(conn, "2026-02")}
    assert ch["NETFLIX COM"]["status"] == "increased" and ch["NETFLIX COM"]["delta_pct"] == 19.0
    assert ch["SPOTIFY"]["status"] == "new"
    assert ch["DEADSUB"]["status"] == "gone"


def test_forecast_projects_and_flags_low():
    conn = _db()
    _txn(conn, norm_desc="BAL", amount=-1, category="Groceries", period_id="2026-03",
         txn_date="2026-03-01", source_account="khaleeji", balance=1000.0)
    plan = [("2026-04", "Salary", "INCOME", 3000), ("2026-04", "Groceries", "FIXED EXPENSES", 500),
            ("2026-05", "Salary", "INCOME", 1000), ("2026-05", "Groceries", "FIXED EXPENSES", 4000)]
    conn.executemany(
        "INSERT INTO plan_lines(period_id,category,section,planned_amount) VALUES(?,?,?,?)", plan)
    cfg = Config(accounts={"sinking_funds": {"school_fees": {"due_periods": ["2026-05"]}}})
    f = fc.forecast(conn, cfg, horizon=2)
    assert f["anchor_period"] == "2026-03" and f["anchor_balance"] == 1000.0
    assert f["rows"][0]["projected_balance"] == 3500.0   # 1000 + (3000-500)
    assert f["rows"][1]["projected_balance"] == 500.0    # 3500 + (1000-4000)
    assert f["low"]["period_id"] == "2026-05" and f["rows"][1]["school_due"] is True
