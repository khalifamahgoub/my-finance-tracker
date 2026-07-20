"""Build the report context (shared by HTML + Markdown) and render the self-contained
HTML dashboard — the one file you open. Default period = the period containing today;
if that's empty, fall back to the latest period with real data and say so in a banner.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import Config
from . import db as dbm
from . import variance as var
from . import sinking as sink
from . import forecast as fc
from .periods import period_id_of, period_label, period_bounds, parse_period
from .theme import TOKENS_CSS

_TEMPLATES = Path(__file__).resolve().parent / "templates"
MIN_MEANINGFUL = 30   # a period with fewer txns is treated as an incomplete tail


def resolve_period(conn: sqlite3.Connection, cfg: Config, explicit: str | None,
                   today: date | None = None) -> tuple[str, str | None]:
    """Return (period_id, banner). banner is set when we fell back off today's period."""
    if explicit:
        return parse_period(explicit), None
    mode = cfg.reporting.get("current_period_mode", "today")
    today_p = period_id_of(today or date.today())
    n_today = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE period_id=?", (today_p,)).fetchone()[0]
    if mode == "today" and n_today > 0:
        return today_p, None
    latest = _latest_meaningful(conn)
    if mode == "today" and latest and latest != today_p:
        banner = (f"No transactions yet for {period_label(today_p)}. "
                  f"Showing the most recent period with data: {period_label(latest)}.")
        return latest, banner
    return (latest or today_p), None


def _latest_meaningful(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT period_id FROM (SELECT period_id, COUNT(*) n FROM transactions "
        "GROUP BY period_id) WHERE n >= ? ORDER BY period_id DESC LIMIT 1",
        (MIN_MEANINGFUL,)).fetchone()
    if row:
        return row[0]
    row = conn.execute("SELECT MAX(period_id) FROM transactions").fetchone()
    return row[0] if row else None


def build_context(conn: sqlite3.Connection, cfg: Config, period_id: str,
                  banner: str | None = None) -> dict:
    summary = var.period_summary(conn, period_id)
    start, end = period_bounds(period_id)
    pva = var.plan_vs_actual(conn, period_id, cfg)
    _over = [r for r in pva if not r["is_income"] and r["variance"] > 1 and r["planned"] > 0]
    top_leak = max(_over, key=lambda r: r["variance"]) if _over else None
    _tids = [r[0] for r in conn.execute(
        "SELECT DISTINCT period_id FROM transactions WHERE period_id<=? "
        "ORDER BY period_id DESC LIMIT 6", (period_id,)).fetchall()]
    net_trend = [{"label": period_label(p), "net": var.period_summary(conn, p)["net"]}
                 for p in reversed(_tids)]
    emergency = sink.emergency_fund(conn, cfg)
    school = sink.school_fund(conn, cfg, period_id)
    surplus = sink.investable_surplus(conn, cfg, period_id, summary["net"])
    n_uncat = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE period_id=? AND category='Uncategorised'",
        (period_id,)).fetchone()[0]
    n_txn = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE period_id=?", (period_id,)).fetchone()[0]
    return {
        "tokens_css": TOKENS_CSS,
        "period_id": period_id,
        "period_label": period_label(period_id),
        "date_range": f"{start.isoformat()} to {end.isoformat()}",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "banner": banner,
        "summary": summary,
        "top_leak": top_leak,
        "net_trend": net_trend,
        "plan_vs_actual": pva,
        "income_rows": [r for r in pva if r["is_income"]],
        "expense_rows": [r for r in pva if not r["is_income"]],
        "breakdown": var.category_breakdown(conn, period_id),
        "flags": var.standing_flags(conn, period_id, cfg),
        "subscriptions": var.subscriptions_list(conn, period_id),
        "sub_changes": var.subscription_changes(conn, period_id),
        "pacing": fc.pacing(conn, cfg, period_id),
        "forecast": fc.forecast(conn, cfg),
        "school": school,
        "emergency": emergency,
        "surplus": surplus,
        "uncategorised": _uncategorised_list(conn, period_id),
        "uncat_count": n_uncat,
        "uncat_pct": round(100 * n_uncat / n_txn, 1) if n_txn else 0,
        "txn_count": n_txn,
    }


def _uncategorised_list(conn: sqlite3.Connection, period_id: str, limit: int = 25) -> list[dict]:
    rows = conn.execute(
        """SELECT COALESCE(counterparty_iban, norm_desc) AS who, COUNT(*) n,
                  -ROUND(SUM(CASE WHEN amount<0 THEN amount ELSE 0 END),3) AS amt,
                  MAX(counterparty_iban) AS iban
           FROM transactions WHERE period_id=? AND category='Uncategorised'
           GROUP BY who ORDER BY amt DESC LIMIT ?""", (period_id, limit)).fetchall()
    return [{"who": r["who"][:46], "n": r["n"], "amt": r["amt"],
             "is_iban": bool(r["iban"])} for r in rows]


def _bhd(v) -> str:
    return f"{v:,.3f}" if isinstance(v, (int, float)) else "—"


def _ragclass(rag: str) -> str:
    return {"🟢": "green", "🟡": "amber", "🔴": "red"}.get(rag, "")


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(_TEMPLATES)),
                      autoescape=select_autoescape(["html"]))
    env.filters["bhd"] = _bhd
    env.filters["ragclass"] = _ragclass
    return env


def render_html(context: dict) -> str:
    return _env().get_template("dashboard.html.j2").render(**context)


def generate(cfg: Config, explicit_period: str | None = None,
             into: Path | None = None) -> tuple[Path, str]:
    """Build + write the dashboard. Returns (path, period_id)."""
    conn = dbm.connect(cfg.db_path)
    period_id, banner = resolve_period(conn, cfg, explicit_period)
    ctx = build_context(conn, cfg, period_id, banner)
    html = render_html(ctx)
    from . import report_md
    md = report_md.render_markdown(ctx)
    conn.close()

    out_dir = into or cfg.output
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "dashboard.html"
    html_path.write_text(html, encoding="utf-8")
    (out_dir / f"summary-{period_id}.md").write_text(md, encoding="utf-8")
    return html_path, period_id
