"""Local interactive dashboard: explore your financials AND tag the review queue.

Stdlib http.server only (no new dependencies) — a small localhost app that reuses the
existing dashboard renderer and the same learning path as `finance review` / Notion pull.

    finance web [--port 8765] [--no-open]

Routes:
  GET  /         live dashboard (regenerated each load) + a floating "tag" link
  GET  /review[?page=N]   the needs-review queue as a paginated form (PAGE_SIZE rows/table;
                          category dropdowns, payee for IBANs)
  POST /apply    learn the submitted tags (IBAN -> iban_map; merchant -> learned.yaml),
                 re-categorise, rewrite output/dashboard.html, redirect back to /review

Bind is 127.0.0.1 only — never exposed off the machine; the data stays local. Shares its
colour tokens with the static dashboard via theme.TOKENS_CSS (one palette, not two).
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
from .theme import TOKENS_CSS

PAGE_SIZE = 40   # rows per table per /review page — keeps each response's DOM bounded


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
# Filled-but-flat, matching the dashboard's own .action treatment (border + weak-tint fill
# + accent text) rather than a solid drop-shadowed chip — same vocabulary, no new chrome.
_PILL = (
    '<a href="/review" style="position:fixed;right:18px;bottom:18px;z-index:99999;'
    'background:var(--accent-weak);color:var(--accent);padding:10px 17px;border-radius:999px;'
    'text-decoration:none;font:600 14px/1 var(--sans);border:1px solid var(--accent)">'
    'Tag {n} uncategorised &rarr;</a>')


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


_REVIEW_CSS = TOKENS_CSS + """
*{box-sizing:border-box} body{margin:0;font:15px/1.5 var(--sans);
 background:var(--bg);color:var(--ink)} main{max-width:920px;margin:0 auto;padding:28px 20px 110px}
h1{font-size:22px;margin:0 0 4px} .sub{color:var(--muted);margin:0 0 22px}
a.back{color:var(--accent);text-decoration:none;font-weight:600;display:inline-block;padding:6px 2px}
.skip-link{position:absolute;left:-9999px;top:0;width:1px;height:1px;overflow:hidden;
 background:var(--card);color:var(--accent);border:1px solid var(--accent);border-radius:0 0 8px 0;
 padding:12px 18px;font-weight:600;text-decoration:none;z-index:1000}
.skip-link:focus{left:0;width:auto;height:auto;overflow:visible}
.flash{background:color-mix(in srgb,var(--green) 14%,var(--card));border:1px solid var(--green);
 color:var(--ink);padding:10px 14px;border-radius:10px;margin:0 0 18px}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:26px 0 8px}
caption{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.pagenav{display:flex;justify-content:space-between;align-items:center;gap:12px;
 padding:10px 0;font-size:13px}
.pagenav a{color:var(--accent);text-decoration:none;font-weight:600}
.tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;min-width:440px} th,td{text-align:left;padding:8px 10px;
 border-bottom:1px solid var(--line);vertical-align:middle} th{font-size:12px;color:var(--muted);
 text-transform:uppercase;letter-spacing:.03em} td.num{text-align:right;font-variant-numeric:tabular-nums;
 white-space:nowrap} .who{font-weight:600} select,input{font:14px var(--sans);padding:11px 10px;
 border:1px solid var(--line);border-radius:8px;background:var(--card);color:inherit;max-width:100%;
 min-height:44px}
input.payee{width:150px;margin-right:6px}
.bar{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--line);
 padding:12px 20px;display:flex;justify-content:center;gap:14px;align-items:center}
button{background:var(--accent);color:var(--card);border:0;border-radius:10px;padding:13px 22px;
 font:600 15px var(--sans);cursor:pointer;min-height:44px}
.muted{color:var(--muted)}
"""


def _options(cats) -> str:
    return "".join(f'<option value="{_html.escape(c)}">{_html.escape(c)}</option>' for c in cats)


def _pagenav(page: int, total_pages: int) -> str:
    prev = (f'<a href="/review?page={page - 1}">&larr; Prev</a>' if page > 1
            else '<span class="muted">&larr; Prev</span>')
    nxt = (f'<a href="/review?page={page + 1}">Next &rarr;</a>' if page < total_pages
           else '<span class="muted">Next &rarr;</span>')
    return f'<div class="pagenav">{prev}<span class="muted">Page {page} of {total_pages}</span>{nxt}</div>'


def review_page(cfg: Config, applied: int | None = None, page: int = 1) -> str:
    conn = dbm.connect(cfg.db_path)
    cats = sorted(c for c in review._valid_categories(conn) if c != "Uncategorised")
    all_ibans = review._iban_groups(conn, 5000)      # fetch all; paginate in Python below
    all_merch = review._merchant_groups(conn, 5000)
    tot = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    unc = conn.execute("SELECT COUNT(*) FROM transactions WHERE category='Uncategorised'").fetchone()[0]
    conn.close()
    opts = _options(cats)

    total_pages = max(1, -(-max(len(all_merch), len(all_ibans)) // PAGE_SIZE))
    page = min(max(1, page), total_pages)
    off = (page - 1) * PAGE_SIZE
    merch = all_merch[off:off + PAGE_SIZE]
    ibans = all_ibans[off:off + PAGE_SIZE]

    rows, i = [], 0
    if merch:
        rows.append(f'<h2>Merchants &nbsp;<span class="muted">{len(all_merch)} total</span></h2>')
        rows.append('<div class="tablewrap"><table><caption>Uncategorised merchants awaiting a '
                    'category</caption><thead><tr><th scope="col">Merchant</th>'
                    '<th scope="col" class="num">Txns</th><th scope="col" class="num">BHD</th>'
                    '<th scope="col">Category</th></tr></thead><tbody>')
        for g in merch:
            rk = g["norm_desc"]
            label = _html.escape(f"Category for {rk[:56]}")
            sel = (f'<select name="cat_{i}" aria-label="{label}">'
                  f'<option value="">&mdash; skip &mdash;</option>{opts}</select>')
            rows.append(
                f'<tr><td class="who">{_html.escape(rk[:52])}</td><td class="num">{g["n"]}</td>'
                f'<td class="num">{g["amt"]:.3f}</td><td>'
                f'<input type="hidden" name="rk_{i}" value="{_html.escape(rk)}">'
                f'<input type="hidden" name="kind_{i}" value="merchant">{sel}</td></tr>')
            i += 1
        rows.append('</tbody></table></div>')

    if ibans:
        rows.append(f'<h2>Bank transfers (IBAN) &nbsp;<span class="muted">{len(all_ibans)} total</span></h2>')
        rows.append('<div class="tablewrap"><table><caption>Uncategorised bank transfers awaiting a '
                    'payee and category</caption><thead><tr><th scope="col">IBAN</th>'
                    '<th scope="col" class="num">Txns</th><th scope="col" class="num">BHD out</th>'
                    '<th scope="col">Payee + Category</th></tr></thead><tbody>')
        for g in ibans:
            rk = g["iban"]
            catlabel = _html.escape(f"Category for IBAN {rk}")
            payeelabel = _html.escape(f"Payee name for IBAN {rk}")
            sel = (f'<select name="cat_{i}" aria-label="{catlabel}">'
                  f'<option value="">&mdash; skip &mdash;</option>{opts}</select>')
            rows.append(
                f'<tr><td class="who">{_html.escape(rk)}</td><td class="num">{g["n"]}</td>'
                f'<td class="num">{g["outflow"]:.3f}</td><td>'
                f'<input type="hidden" name="rk_{i}" value="{_html.escape(rk)}">'
                f'<input type="hidden" name="kind_{i}" value="iban">'
                f'<input class="payee" name="payee_{i}" placeholder="payee" aria-label="{payeelabel}">'
                f'{sel}</td></tr>')
            i += 1
        rows.append('</tbody></table></div>')

    flash = (f'<div class="flash" role="status" aria-live="polite">Applied {applied} tag(s). '
            f'Uncategorised now {unc}/{tot} ({100 * unc / tot if tot else 0:.1f}%).</div>'
            if applied is not None else '')
    nav = _pagenav(page, total_pages) if (all_merch or all_ibans) else ''
    body = "".join(rows) if (merch or ibans) else '<p class="muted">Nothing on this page needs review.</p>'
    empty = '<p class="muted">Nothing to review. 🎉</p>' if not (all_merch or all_ibans) else ''
    return (
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>Review &amp; tag</title><style>{_REVIEW_CSS}</style></head><body>'
        f'<a class="skip-link" href="#apply-btn">Skip to Apply tags</a><main>'
        f'<a class="back" href="/">&larr; Dashboard</a>'
        f'<h1>Review &amp; tag</h1><p class="sub">Pick a category to learn it permanently; '
        f'blank rows are skipped. {unc} uncategorised of {tot}.</p>{flash}{nav}'
        f'<form method="POST" action="/apply">'
        f'<input type="hidden" name="_page" value="{page}">{body}{empty}'
        f'<div class="bar"><span class="muted">Tags apply to all matching transactions and future imports.</span>'
        f'<button type="submit" id="apply-btn">Apply tags</button></div></form></main></body></html>')


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
                page = int(q["page"][0]) if q.get("page", [""])[0].isdigit() else 1
                self._send(200, review_page(self.cfg, applied, page))
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
        page = form.get("_page", ["1"])[0]
        self.send_response(303)
        self.send_header("Location", f"/review?page={page}&applied={applied}")
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
