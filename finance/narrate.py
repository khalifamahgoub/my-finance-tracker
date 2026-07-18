"""`finance narrate [period]` — an optional AI narrative layered on the deterministic
numbers (never replacing them). Feeds the report context to the Claude API for a blunt
3-line what-changed / what-to-watch / what-to-do summary.

ANTHROPIC_API_KEY is read from the environment and never handled by this code beyond the
request header. `--dry-run` prints the exact prompt and sends nothing.
"""
from __future__ import annotations

import json
import os

from .config import Config
from . import db as dbm
from . import report_html

API = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"   # cheap + fast for a monthly summary


def _facts(ctx: dict) -> str:
    s = ctx["summary"]
    over = [f"{r['category']} {r['actual']:.0f} vs {r['planned']:.0f} plan"
            for r in ctx["expense_rows"] if r["variance"] > 1 and r["planned"] > 0][:5]
    flags = [f"{f['category']} {f['actual']:.0f}"
             + (f" ({f['vs_avg_pct']:+.0f}% vs 3-mo)" if f["vs_avg_pct"] is not None else "")
             for f in ctx["flags"]]
    subs = [f"{r['merchant'][:24]} {r['status']}"
            + (f" {r['delta_pct']:+.0f}%" if r["delta_pct"] else "")
            for r in ctx.get("sub_changes", []) if r["status"] in ("increased", "new", "gone")][:5]
    fcast = ctx.get("forecast") or {}
    low = fcast.get("low")
    pace = ctx.get("pacing") or {}
    facts = {
        "period": ctx["period_label"],
        "income": s["income"], "spend": s["spend"], "net": s["net"],
        "savings_rate_pct": s["savings_rate"],
        "over_plan": over,
        "standing_flags": flags,
        "subscription_changes": subs,
        "pace": ({"day": pace.get("day"), "of": pace.get("days"),
                  "projected_spend": pace.get("projected_spend"),
                  "plan_expense": pace.get("plan_expense")} if pace else None),
        "forecast_low_point": (f"{low['label']} balance {low['projected_balance']:.0f}"
                               + (" (term fee)" if low.get("school_due") else "")) if low else None,
        "uncategorised_pct": ctx["uncat_pct"],
    }
    return json.dumps(facts, indent=2)


def build_prompt(ctx: dict) -> str:
    return (
        "You are a blunt personal-finance analyst. Money is in BHD; the financial month "
        "runs 23rd-22nd. Given the facts below, write EXACTLY three short lines, no preamble, "
        "no restating the numbers verbatim:\n"
        "1. What changed this period (the single most important shift)\n"
        "2. What to watch (the biggest risk or leak)\n"
        "3. What to do (one concrete action this week)\n\n"
        f"FACTS:\n{_facts(ctx)}"
    )


def narrate(cfg: Config, period: str | None = None, dry_run: bool = False,
            model: str | None = None) -> int:
    conn = dbm.connect(cfg.db_path)
    period_id, banner = report_html.resolve_period(conn, cfg, period)
    ctx = report_html.build_context(conn, cfg, period_id, banner)
    conn.close()
    prompt = build_prompt(ctx)

    if dry_run:
        print(prompt)
        return 0

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("ANTHROPIC_API_KEY is not set. To enable the AI narrative:")
        print("  set ANTHROPIC_API_KEY=<your key>  and re-run  finance narrate")
        print("  (or use --dry-run to see the exact prompt without calling the API)")
        return 1

    try:
        import truststore
        truststore.inject_into_ssl()   # TLS-inspecting proxy: OS trust store
    except Exception:
        pass
    import requests

    model = model or (cfg.reporting.get("narrate_model") or DEFAULT_MODEL)
    r = requests.post(API, timeout=60, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json",
    }, json={"model": model, "max_tokens": 300,
             "messages": [{"role": "user", "content": prompt}]})
    if r.status_code >= 400:
        print(f"Claude API error {r.status_code}: {r.text[:300]}")
        return 1
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    print(f"\n{ctx['period_label']} — narrative ({model}):\n")
    print(text.strip())
    return 0
