"""Markdown monthly summary for pasting into Notion. Blunt tone, findings first, no filler.
Consumes the same context dict as the HTML dashboard."""
from __future__ import annotations


def _m(v) -> str:
    return f"{v:,.3f}" if isinstance(v, (int, float)) else "—"


def render_markdown(ctx: dict) -> str:
    s = ctx["summary"]
    L: list[str] = []
    L.append(f"# Finance — {ctx['period_label']}")
    L.append(f"_{ctx['date_range']} · financial month (23rd–22nd) · {ctx['txn_count']} txns_")
    if ctx.get("banner"):
        L.append(f"> {ctx['banner']}")
    L.append("")
    net = s["net"]
    L.append(f"**Income {_m(s['income'])} · Spend {_m(s['spend'])} · "
             f"Net {_m(net)} ({'surplus' if net >= 0 else 'DEFICIT'}) · "
             f"Savings rate {s['savings_rate']}%**")
    L.append(f"**Investable surplus (suggested transfer): {_m(ctx['surplus']['suggested_transfer'])}** "
             f"(net minus {_m(ctx['surplus']['school_set_aside'])} school set-aside)")
    p = ctx.get("pacing")
    if p:
        tail = ("final" if p["closed"] else f"on track for ~{_m(p['projected_spend'])}")
        L.append(f"**Pace** — day {p['day']}/{p['days']}: spent {_m(p['spend_to_date'])} so far, "
                 f"{tail} vs plan {_m(p['plan_expense'])}.")
    L.append("")

    # Biggest over-plan (the findings)
    over = sorted([r for r in ctx["expense_rows"] if r["variance"] > 1 and r["planned"] > 0],
                  key=lambda r: r["variance"], reverse=True)[:5]
    if over:
        L.append("## Over plan")
        for r in over:
            L.append(f"- {r['rag']} **{r['category']}** {_m(r['actual'])} vs {_m(r['planned'])} "
                     f"plan (+{_m(r['variance'])})")
        L.append("")
    unplanned = [r for r in ctx["expense_rows"] if r["section"] == "UNPLANNED"]
    if unplanned:
        L.append("## Unplanned spend")
        for r in unplanned[:6]:
            L.append(f"- {r['category']} {_m(r['actual'])}")
        L.append("")

    L.append("## Standing flags")
    for f in ctx["flags"]:
        avg = f"3-mo avg {_m(f['avg_3mo'])}" if f["avg_3mo"] is not None else "no history"
        vs = f" ({'+' if (f['vs_avg_pct'] or 0) >= 0 else ''}{f['vs_avg_pct']}% vs avg)" if f["vs_avg_pct"] is not None else ""
        plan = f", plan {_m(f['planned'])}" if f["planned"] else ""
        L.append(f"- **{f['category']}** {_m(f['actual'])}{vs} — {avg}{plan}")
    L.append("")

    subs = [s for s in ctx.get("sub_changes", []) if s["status"] in ("increased", "new", "gone")]
    if subs:
        L.append("## Subscription changes")
        for s in subs[:8]:
            d = f" ({s['delta_pct']:+.0f}%)" if s["delta_pct"] else ""
            L.append(f"- {s['status'].upper()}: {s['merchant'][:30]} {_m(s['amount'])}{d}")
        L.append("")

    fcast = ctx.get("forecast") or {}
    if fcast.get("rows"):
        low = fcast.get("low")
        L.append("## Cash-flow forecast")
        L.append(f"From {fcast['anchor_label']} balance {_m(fcast['anchor_balance'])}, following the plan:")
        for r in fcast["rows"]:
            mark = "  <- low point" if low and r["period_id"] == low["period_id"] else ""
            tag = " (term fee)" if r["school_due"] else ""
            L.append(f"- {r['label']}{tag}: net {_m(r['planned_net'])} -> balance {_m(r['projected_balance'])}{mark}")
        L.append("")

    sch, em = ctx["school"], ctx["emergency"]
    L.append("## Sinking & savings")
    L.append(f"- **{sch['label']}**: target {_m(sch['target'])}, next due {sch['next_due'] or '—'} "
             f"→ set aside {_m(sch['suggested_monthly'])}/mo")
    L.append(f"- **{em['label']}**: {_m(em['current'])} / {_m(em['target'])} ({em['pct']}%), "
             f"shortfall {_m(em['shortfall'])}")
    L.append("")

    L.append(f"## Needs review — {ctx['uncat_count']} uncategorised ({ctx['uncat_pct']}%)")
    for u in ctx["uncategorised"][:8]:
        tag = " (IBAN)" if u["is_iban"] else ""
        L.append(f"- {u['who']}{tag} — {u['n']}×, {_m(u['amt'])}")
    L.append("")
    L.append(f"_Generated {ctx['generated_at']} · `finance run`_")
    return "\n".join(L) + "\n"
