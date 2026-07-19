"""Local interactive dashboard: explore your financials AND tag the review queue.

Stdlib http.server only (no new dependencies) — a small localhost app that reuses the
existing dashboard renderer and the same learning path as `finance review` / Notion pull.

    finance web [--port 8765] [--no-open]

Routes:
  GET  /         live dashboard (regenerated each load) + a floating "tag" link
  GET  /review   the needs-review queue as a form (category dropdowns; payee for IBANs)
  POST /apply    learn the submitted tags (IBAN -> iban_map; merchant -> learned.yaml),
                 re-categorise, rewrite output/dashboard.html, redirect back to /review

Bind is 127.0.0.1 only — never exposed off the machine; the data stays local.
"""
from __future__ import annotations

import argparse
import html as _html
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .config import Config
from . import db as dbm, review, categorise, report_html


# ---- core logic (pure-ish; unit-tested) -------------------------------------
def parse_entries(form: dict) -> list[dict]:
    """parse_qs dict -> [{rk, is_iban, category, payee}], one per rk_<i> field."""
    def g(k):
        v = form.get(k)
        return (v[0] if v else "").strip()

    entries = []
    for i in sorted({int(k[3:]) for k in form if k.startswith("rk_") and k[3:].isdigit()}):
        rk = g(f"rk_{i}")
        if not rk:
            continue
        entries.append({"rk": rk, "is_iban": g(f"kind_{i}") == "iban",
                        "category": g(f"cat_{i}"), "payee": g(f"payee_{i}")})
    return entries


def apply_and_recategorise(cfg: Config, entries: list[dict], reload_cfg=None) -> tuple[int, dict]:
    """Learn each tagged entry the same way `finance review` does, then re-categorise.
    Returns (applied_count, categorise counts). Invalid/blank categories are skipped.
    """
    conn = dbm.connect(cfg.db_path)
    valid = review._valid_categories(conn)
    applied = 0
    for e in entries:
        cat = (e.get("category") or "").strip()
        if not cat or cat not in valid:
            continue
        if e.get("is_iban"):
            review._confirm_iban(conn, e["rk"], (e.get("payee") or "").strip() or None, cat)
        else:
            key = review._key_from(e["rk"])
            if not key:                    # location/annotation line: not learnable
                continue
            review._learn_keyword(key, cat)
        applied += 1
    conn.commit()
    fresh = (reload_cfg or Config.load)()          # reload so learned.yaml keywords apply
    counts = categorise.categorise_all(conn, fresh)
    conn.close()
    return applied, counts


def _uncategorised_pct(counts: dict) -> float:
    return 100 * counts["uncategorised"] / counts["total"] if counts.get("total") else 0.0


# ---- page rendering ---------------------------------------------------------
_PILL = (
    '<a href="/review" style="position:fixed;right:18px;bottom:18px;z-index:99999;'
    'background:#3b5bdb;color:#fff;padding:11px 18px;border-radius:999px;'
    'text-decoration:none;font:600 14px/1 system-ui,sans-serif;'
    'box-shadow:0 6px 20px rgba(0,0,0,.28)">Tag {n} uncategorised &rarr;</a>')


def dashboard_page(cfg: Config) -> str:
    conn = dbm.connect(cfg.db_path)
    period_id, banner = report_html.resolve_period(conn, cfg, None)
    ctx = report_html.build_context(conn, cfg, period_id, banner)
    n = conn.execute(
        "SELECT COUNT(DISTINCT COALESCE(counterparty_iban, norm_desc)) FROM transactions "
        "WHERE needs_review=1 AND category='Uncategorised'").fetchone()[0]
    conn.close()
    html = report_html.render_html(ctx)
    pill = _PILL.format(n=n) if n else ""
    return html.replace("</body>", pill + "</body>") if "</body>" in html else html + pill


_REVIEW_CSS = """
*{box-sizing:border-box} body{margin:0;font:15px/1.5 system-ui,-apple-system,sans-serif;
 background:#f6f7f9;color:#14161a} main{max-width:920px;margin:0 auto;padding:28px 20px 96px}
h1{font-size:22px;margin:0 0 4px} .sub{color:#6b7280;margin:0 0 22px}
a.back{color:#3b5bdb;text-decoration:none;font-weight:600}
.flash{background:#e7f5ec;border:1px solid #b7e4c7;color:#1b5e34;padding:10px 14px;
 border-radius:10px;margin:0 0 18px}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.04em;color:#6b7280;margin:26px 0 8px}
table{width:100%;border-collapse:collapse} th,td{text-align:left;padding:8px 10px;
 border-bottom:1px solid #e6e8eb;vertical-align:middle} th{font-size:12px;color:#6b7280;
 text-transform:uppercase;letter-spacing:.03em} td.num{text-align:right;font-variant-numeric:tabular-nums;
 white-space:nowrap} .who{font-weight:600} select,input{font:14px system-ui;padding:6px 8px;
 border:1px solid #cfd4da;border-radius:8px;background:#fff;color:inherit;max-width:100%}
input.payee{width:150px;margin-right:6px}
.bar{position:fixed;left:0;right:0;bottom:0;background:#fff;border-top:1px solid #e6e8eb;
 padding:12px 20px;display:flex;justify-content:center;gap:14px;align-items:center}
button{background:#3b5bdb;color:#fff;border:0;border-radius:10px;padding:11px 22px;
 font:600 15px system-ui;cursor:pointer} .muted{color:#6b7280}
@media (prefers-color-scheme:dark){body{background:#0f1115;color:#e6e8eb}
 select,input{background:#171a21;border-color:#2b2f37} th,td{border-color:#20242c}
 .bar{background:#12151b;border-color:#20242c} .sub,h2,.muted{color:#9aa2ad}
 .flash{background:#0f2a1a;border-color:#1f5136;color:#8ce0aa}}
"""


def _options(cats) -> str:
    return "".join(f'<option value="{_html.escape(c)}">{_html.escape(c)}</option>' for c in cats)


def review_page(cfg: Config, applied: int | None = None) -> str:
    conn = dbm.connect(cfg.db_path)
    cats = sorted(c for c in review._valid_categories(conn) if c != "Uncategorised")
    ibans = review._iban_groups(conn, 300)
    merch = review._merchant_groups(conn, 300)
    tot = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    unc = conn.execute("SELECT COUNT(*) FROM transactions WHERE category='Uncategorised'").fetchone()[0]
    conn.close()
    opts = _options(cats)

    rows, i = [], 0
    rows.append(f'<h2>Merchants &nbsp;<span class="muted">{len(merch)}</span></h2>')
    rows.append('<table><tr><th>Merchant</th><th>Txns</th><th>BHD</th><th>Category</th></tr>')
    for g in merch:
        rk = g["norm_desc"]
        sel = f'<select name="cat_{i}"><option value="">&mdash; skip &mdash;</option>{opts}</select>'
        rows.append(
            f'<tr><td class="who">{_html.escape(rk[:52])}</td><td class="num">{g["n"]}</td>'
            f'<td class="num">{g["amt"]:.3f}</td><td>'
            f'<input type="hidden" name="rk_{i}" value="{_html.escape(rk)}">'
            f'<input type="hidden" name="kind_{i}" value="merchant">{sel}</td></tr>')
        i += 1
    rows.append('</table>')

    if ibans:
        rows.append(f'<h2>Bank transfers (IBAN) &nbsp;<span class="muted">{len(ibans)}</span></h2>')
        rows.append('<table><tr><th>IBAN</th><th>Txns</th><th>BHD out</th><th>Payee + Category</th></tr>')
        for g in ibans:
            rk = g["iban"]
            sel = f'<select name="cat_{i}"><option value="">&mdash; skip &mdash;</option>{opts}</select>'
            rows.append(
                f'<tr><td class="who">{_html.escape(rk)}</td><td class="num">{g["n"]}</td>'
                f'<td class="num">{g["outflow"]:.3f}</td><td>'
                f'<input type="hidden" name="rk_{i}" value="{_html.escape(rk)}">'
                f'<input type="hidden" name="kind_{i}" value="iban">'
                f'<input class="payee" name="payee_{i}" placeholder="payee">{sel}</td></tr>')
            i += 1
        rows.append('</table>')

    flash = f'<div class="flash">Applied {applied} tag(s). Uncategorised now {unc}/{tot} ({100*unc/tot if tot else 0:.1f}%).</div>' if applied is not None else ''
    body = "".join(rows) if (merch or ibans) else '<p class="muted">Nothing needs review. 🎉</p>'
    return (
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>Review &amp; tag</title><style>{_REVIEW_CSS}</style></head><body><main>'
        f'<a class="back" href="/">&larr; Dashboard</a>'
        f'<h1>Review &amp; tag</h1><p class="sub">Pick a category to learn it permanently; '
        f'blank rows are skipped. {unc} uncategorised of {tot}.</p>{flash}'
        f'<form method="POST" action="/apply">{body}'
        f'<div class="bar"><span class="muted">Tags apply to all matching transactions and future imports.</span>'
        f'<button type="submit">Apply tags</button></div></form></main></body></html>')


# ---- HTTP server ------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    cfg: Config = None  # set by serve()

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        if b:
            self.wfile.write(b)

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send(200, dashboard_page(self.cfg))
            elif parsed.path == "/review":
                q = parse_qs(parsed.query)
                applied = int(q["applied"][0]) if "applied" in q else None
                self._send(200, review_page(self.cfg, applied))
            elif parsed.path == "/favicon.ico":
                self._send(204, b"")
            else:
                self._send(404, "not found", "text/plain")
        except Exception as e:                      # keep the server up; show the error
            self._send(500, f"<pre>{_html.escape(repr(e))}</pre>")

    def do_POST(self):
        if urlparse(self.path).path != "/apply":
            self._send(404, "not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8")) if length else {}
        applied, _ = apply_and_recategorise(self.cfg, parse_entries(form))
        report_html.generate(self.cfg)             # keep the static dashboard.html in sync
        self.send_response(303)
        self.send_header("Location", f"/review?applied={applied}")
        self.end_headers()

    def log_message(self, *args):                  # silence per-request logging
        pass


def serve(cfg: Config, port: int = 8765, open_browser: bool = True) -> int:
    _Handler.cfg = cfg
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Finance dashboard on {url}   (explore + tag; Ctrl-C to stop)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        httpd.server_close()
    return 0
