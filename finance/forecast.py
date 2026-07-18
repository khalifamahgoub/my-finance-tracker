"""Forward-looking views (the dimension the tracker was missing):

- forecast(): project end-of-period bank balances forward from the latest known balance
  using the plan's net per period, and flag the low point + school-fee due periods.
- pacing(): month-to-date burn for the rendered period — spend so far, extrapolated
  full-period spend, vs plan and vs last period. Turns a monthly report into a weekly ritual.

Both reuse existing plumbing (plan_lines, sinking balances, periods) — no new data.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from .config import Config
from . import sinking as sink
from . import variance as var
from .periods import period_bounds, period_label, next_period_id, period_id_of


def plan_net(conn: sqlite3.Connection, period_id: str) -> dict:
    """Planned income, expenses and net for a period from plan_lines."""
    rows = conn.execute(
        "SELECT section, ROUND(SUM(planned_amount),3) s FROM plan_lines "
        "WHERE period_id=? GROUP BY section", (period_id,)).fetchall()
    income = expense = 0.0
    for r in rows:
        if (r["section"] or "").upper() == "INCOME":
            income += r["s"]
        else:
            expense += r["s"]
    return {"income": round(income, 3), "expense": round(expense, 3),
            "net": round(income - expense, 3)}


def _latest_data_period(conn: sqlite3.Connection) -> str | None:
    r = conn.execute("SELECT MAX(period_id) FROM transactions").fetchone()
    return r[0] if r else None


def forecast(conn: sqlite3.Connection, cfg: Config, horizon: int = 6) -> dict:
    """Project the next `horizon` periods' end balances from the latest actual balance."""
    balances = sink.latest_bank_balances(conn)
    anchor_balance = balances.get("total", 0.0)
    anchor = _latest_data_period(conn)
    due = set((cfg.sinking_funds.get("school_fees", {}) or {}).get("due_periods", []) or [])

    rows: list[dict] = []
    bal = anchor_balance
    p = anchor
    for _ in range(horizon):
        if not p:
            break
        p = next_period_id(p)
        pn = plan_net(conn, p)
        bal = round(bal + pn["net"], 3)
        rows.append({"period_id": p, "label": period_label(p), "planned_net": pn["net"],
                     "projected_balance": bal, "school_due": p in due})
    low = min(rows, key=lambda r: r["projected_balance"]) if rows else None
    return {"anchor_period": anchor, "anchor_label": period_label(anchor) if anchor else None,
            "anchor_balance": anchor_balance, "rows": rows, "low": low}


def pacing(conn: sqlite3.Connection, cfg: Config, period_id: str,
           today: date | None = None) -> dict | None:
    """Month-to-date spend + extrapolated full-period spend for the rendered period."""
    start, end = period_bounds(period_id)
    today = today or date.today()
    as_of = min(today, end)
    if as_of < start:
        return None  # period hasn't started
    total_days = (end - start).days + 1
    elapsed = (as_of - start).days + 1
    frac = elapsed / total_days
    spend_to_date = conn.execute(
        """SELECT -ROUND(SUM(t.amount),3) FROM transactions t JOIN categories c
           ON c.name=t.category WHERE t.period_id=? AND t.is_internal=0
           AND c.kind IN ('expense','sinking') AND t.amount<0 AND t.txn_date<=?""",
        (period_id, as_of.isoformat())).fetchone()[0] or 0.0
    projected = round(spend_to_date / frac, 3) if frac > 0 else spend_to_date
    plan_exp = plan_net(conn, period_id)["expense"]
    prior = conn.execute(
        "SELECT MAX(period_id) FROM transactions WHERE period_id < ?", (period_id,)).fetchone()[0]
    last_spend = var.period_summary(conn, prior)["spend"] if prior else None
    return {
        "closed": today > end, "day": elapsed, "days": total_days,
        "spend_to_date": round(spend_to_date, 3), "projected_spend": projected,
        "plan_expense": plan_exp, "last_period": period_label(prior) if prior else None,
        "last_spend": last_spend,
        "on_track": (projected <= plan_exp) if plan_exp else None,
    }
