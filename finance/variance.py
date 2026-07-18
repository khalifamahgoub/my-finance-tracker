"""Plan-vs-actual, RAG status, standing flags, and period headline numbers.

Actuals roll transaction categories up to plan labels via categories.plan_category,
with a sign flip by kind (income +, expense/sinking -) so magnitudes are comparable to
the plan's positive figures. Internal transfers are excluded everywhere.
"""
from __future__ import annotations

import sqlite3

from .config import Config

GREEN, AMBER, RED = "🟢", "🟡", "🔴"


def actual_by_plan(conn: sqlite3.Connection, period_id: str) -> dict[str, float]:
    rows = conn.execute(
        """SELECT cat.plan_category AS pl, cat.kind AS kind, ROUND(SUM(t.amount), 3) AS s
           FROM transactions t JOIN categories cat ON cat.name = t.category
           WHERE t.period_id = ? AND t.is_internal = 0 AND cat.plan_category IS NOT NULL
           GROUP BY cat.plan_category, cat.kind""", (period_id,)).fetchall()
    out: dict[str, float] = {}
    for r in rows:
        mag = r["s"] if r["kind"] == "income" else -r["s"]
        out[r["pl"]] = round(out.get(r["pl"], 0.0) + mag, 3)
    return out


def _rag(planned: float, actual: float, is_income: bool, amber_pct: float) -> str:
    if planned <= 0:
        return AMBER if actual > 0 else GREEN          # unplanned spend/income -> watch
    ratio = actual / planned
    if is_income:
        if ratio >= 1:
            return GREEN
        return AMBER if ratio >= 1 - amber_pct / 100 else RED
    if actual <= planned:
        return GREEN
    return AMBER if ratio <= 1 + amber_pct / 100 else RED


def plan_vs_actual(conn: sqlite3.Connection, period_id: str, cfg: Config) -> list[dict]:
    amber_pct = float(cfg.reporting.get("rag_amber_pct", 15))
    actual = actual_by_plan(conn, period_id)
    rows = conn.execute(
        "SELECT category, section, planned_amount FROM plan_lines WHERE period_id=? "
        "ORDER BY section DESC, category", (period_id,)).fetchall()
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        label = r["category"]
        seen.add(label)
        a = actual.get(label, 0.0)
        planned = r["planned_amount"]
        is_income = (r["section"] or "").upper() == "INCOME"
        out.append({
            "category": label, "section": r["section"], "is_income": is_income,
            "planned": planned, "actual": round(a, 3),
            "variance": round(a - planned, 3),
            "pct": round(100 * a / planned, 1) if planned else None,
            "rag": _rag(planned, a, is_income, amber_pct),
        })
    # actuals with no matching plan line (unplanned)
    for label, a in actual.items():
        if label not in seen and abs(a) > 0.001:
            out.append({"category": label, "section": "UNPLANNED", "is_income": False,
                        "planned": 0.0, "actual": round(a, 3), "variance": round(a, 3),
                        "pct": None, "rag": AMBER})
    return out


def period_summary(conn: sqlite3.Connection, period_id: str) -> dict:
    income = conn.execute(
        """SELECT ROUND(SUM(t.amount),3) FROM transactions t JOIN categories cat
           ON cat.name=t.category WHERE t.period_id=? AND t.is_internal=0
           AND cat.kind='income' AND t.amount>0""", (period_id,)).fetchone()[0] or 0.0
    spend = conn.execute(
        """SELECT -ROUND(SUM(t.amount),3) FROM transactions t JOIN categories cat
           ON cat.name=t.category WHERE t.period_id=? AND t.is_internal=0
           AND cat.kind IN ('expense','sinking') AND t.amount<0""",
        (period_id,)).fetchone()[0] or 0.0
    net = round(income - spend, 3)
    return {"period_id": period_id, "income": round(income, 3), "spend": round(spend, 3),
            "net": net, "savings_rate": round(100 * net / income, 1) if income else None}


def category_breakdown(conn: sqlite3.Connection, period_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT t.category AS category, -ROUND(SUM(t.amount),3) AS spend, COUNT(*) AS n
           FROM transactions t JOIN categories cat ON cat.name=t.category
           WHERE t.period_id=? AND t.is_internal=0 AND cat.kind IN ('expense','sinking')
             AND t.amount<0
           GROUP BY t.category ORDER BY spend DESC""", (period_id,)).fetchall()
    total = sum(r["spend"] for r in rows) or 1.0
    return [{"category": r["category"], "spend": r["spend"], "n": r["n"],
             "pct": round(100 * r["spend"] / total, 1)} for r in rows]


def _prior_periods(conn: sqlite3.Connection, period_id: str, k: int) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT period_id FROM transactions WHERE period_id < ? "
        "ORDER BY period_id DESC LIMIT ?", (period_id, k)).fetchall()
    return [r[0] for r in rows]


def _category_spend(conn: sqlite3.Connection, period_id: str, category: str) -> float:
    return conn.execute(
        "SELECT -ROUND(SUM(amount),3) FROM transactions WHERE period_id=? AND category=? "
        "AND is_internal=0 AND amount<0", (period_id, category)).fetchone()[0] or 0.0


def standing_flags(conn: sqlite3.Connection, period_id: str, cfg: Config) -> list[dict]:
    """Food Delivery / Telecom / Subscriptions: actual vs 3-month average vs plan."""
    flags = cfg.reporting.get("standing_flags", ["Food Delivery", "Telecom", "Subscriptions"])
    priors = _prior_periods(conn, period_id, 3)
    plan_map = dict(conn.execute(
        "SELECT category, planned_amount FROM plan_lines WHERE period_id=?", (period_id,)).fetchall())
    plan_cat = {r["name"]: r["plan_category"] for r in conn.execute(
        "SELECT name, plan_category FROM categories").fetchall()}
    out = []
    for cat in flags:
        actual = _category_spend(conn, period_id, cat)
        avg3 = (round(sum(_category_spend(conn, p, cat) for p in priors) / len(priors), 3)
                if priors else None)
        planned = plan_map.get(plan_cat.get(cat)) if plan_cat.get(cat) else None
        out.append({"category": cat, "actual": actual, "avg_3mo": avg3, "planned": planned,
                    "vs_avg_pct": round(100 * (actual - avg3) / avg3, 1) if avg3 else None})
    return out


def subscriptions_list(conn: sqlite3.Connection, period_id: str) -> list[dict]:
    """Enumerate individual subscriptions so dead ones surface (PRD 6)."""
    rows = conn.execute(
        """SELECT norm_desc, -ROUND(SUM(amount),3) AS amt, COUNT(*) AS n
           FROM transactions WHERE period_id=? AND category='Subscriptions' AND is_internal=0
           GROUP BY norm_desc ORDER BY amt DESC""", (period_id,)).fetchall()
    return [{"merchant": r["norm_desc"], "amount": r["amt"], "n": r["n"]} for r in rows]


def _sub_key(norm_desc: str) -> str:
    """Canonical merchant key: first two non-numeric tokens, so a merchant whose billing
    city changes month to month (e.g. 'NETFLIX COM AMSTERDAM' vs '... LOS GATOS') still
    matches across periods instead of looking like a cancel + re-subscribe."""
    toks = [t for t in norm_desc.split() if not t.isdigit()]
    return " ".join(toks[:2]) if toks else norm_desc


def _sub_amounts(conn: sqlite3.Connection, period_id: str) -> dict[str, float]:
    """Subscription charges by canonical merchant, excluding the FX markup/VAT fee lines."""
    rows = conn.execute(
        """SELECT norm_desc, -ROUND(SUM(amount),3) AS amt
           FROM transactions WHERE period_id=? AND category='Subscriptions' AND is_internal=0
             AND norm_desc NOT LIKE 'FOREIGN EXCHANGE%' AND norm_desc NOT LIKE 'VAT %'
           GROUP BY norm_desc""", (period_id,)).fetchall()
    out: dict[str, float] = {}
    for r in rows:
        k = _sub_key(r["norm_desc"])
        out[k] = round(out.get(k, 0.0) + r["amt"], 3)
    return out


def subscription_changes(conn: sqlite3.Connection, period_id: str) -> list[dict]:
    """Compare each subscription to the prior period: new / increased / decreased / gone.
    Price creep (silent hikes) and vanished-but-maybe-still-billed subs both surface."""
    prior = conn.execute(
        "SELECT MAX(period_id) FROM transactions WHERE period_id < ?", (period_id,)).fetchone()[0]
    cur = _sub_amounts(conn, period_id)
    prv = _sub_amounts(conn, prior) if prior else {}
    out: list[dict] = []
    for m, amt in cur.items():
        p = prv.get(m)
        if p is None:
            status, delta = "new", None
        elif amt > p * 1.02:
            status, delta = "increased", round(100 * (amt - p) / p, 1)
        elif amt < p * 0.98:
            status, delta = "decreased", round(100 * (amt - p) / p, 1)
        else:
            status, delta = "steady", 0.0
        out.append({"merchant": m, "amount": amt, "prev": p, "status": status, "delta_pct": delta})
    for m, p in prv.items():
        if m not in cur:
            out.append({"merchant": m, "amount": 0.0, "prev": p, "status": "gone", "delta_pct": None})
    order = {"increased": 0, "new": 1, "gone": 2, "decreased": 3, "steady": 4}
    out.sort(key=lambda r: (order[r["status"]], -(r["amount"] or 0)))
    return out
