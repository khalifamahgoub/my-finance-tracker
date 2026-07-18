"""Sinking-fund + bank-balance tracking.

Term fees are lump sums three times a year (Mar/Jul/Dec), so we track a set-aside
toward the next due term fee rather than treating them as monthly spend. The emergency
fund is measured against liquid bank balances (no dedicated account yet).
"""
from __future__ import annotations

import sqlite3

from .config import Config
from .periods import period_bounds


def _months_between(from_period: str, to_period: str) -> int:
    fy, fm = map(int, from_period.split("-"))
    ty, tm = map(int, to_period.split("-"))
    return (ty - fy) * 12 + (tm - fm)


def latest_bank_balances(conn: sqlite3.Connection) -> dict:
    """Most recent running balance per account that supplies one (khaleeji, ila_account)."""
    out: dict[str, float] = {}
    for src in ("khaleeji", "ila_account"):
        row = conn.execute(
            "SELECT balance FROM transactions WHERE source_account=? AND balance IS NOT NULL "
            "ORDER BY txn_date DESC, txn_id DESC LIMIT 1", (src,)).fetchone()
        if row and row[0] is not None:
            out[src] = round(row[0], 3)
    out["total"] = round(sum(out.values()), 3)
    return out


def school_fund(conn: sqlite3.Connection, cfg: Config, period_id: str) -> dict:
    funds = cfg.sinking_funds.get("school_fees", {}) or {}
    target = float(funds.get("target_amount", 3990))
    due_periods = funds.get("due_periods", []) or []
    next_due = next((p for p in sorted(due_periods) if p >= period_id), None)
    months = _months_between(period_id, next_due) if next_due else None
    suggested = round(target / months, 3) if months and months > 0 else (target if next_due else 0.0)
    # term fees actually paid this calendar year (is_sinking draws)
    year = period_id[:4]
    paid = conn.execute(
        "SELECT -ROUND(SUM(amount),3) FROM transactions WHERE is_sinking=1 AND amount<0 "
        "AND substr(txn_date,1,4)=?", (year,)).fetchone()[0] or 0.0
    return {"label": funds.get("label", "Term fund"), "target": target,
            "next_due": next_due, "months_to_due": months,
            "suggested_monthly": suggested, "paid_this_year": round(paid, 3)}


def emergency_fund(conn: sqlite3.Connection, cfg: Config) -> dict:
    funds = cfg.sinking_funds.get("emergency", {}) or {}
    target = float(funds.get("target_amount", 10000))
    balances = latest_bank_balances(conn)
    current = balances.get("total", 0.0)
    return {"label": funds.get("label", "Emergency / Savings"), "target": target,
            "current": current, "pct": round(100 * current / target, 1) if target else None,
            "shortfall": round(max(0.0, target - current), 3), "balances": balances}


def investable_surplus(conn: sqlite3.Connection, cfg: Config, period_id: str,
                       net: float) -> dict:
    """Net after the school set-aside = suggested transfer to invest (e.g. a brokerage)."""
    school = school_fund(conn, cfg, period_id)
    set_aside = school["suggested_monthly"]
    surplus = round(max(0.0, net - set_aside), 3)
    return {"net": net, "school_set_aside": set_aside, "suggested_transfer": surplus}
