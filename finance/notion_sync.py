"""SQLite -> Notion projection (Phase 4). SQLite stays the source of truth; Notion is a
derived, browsable, AI-queryable mirror. The push never feeds back — with ONE scoped
exception: the Review Queue is a two-way surface. You fill its Category (and Payee, for
IBANs) column in Notion, and `sync-notion --pull` reads those edits back and learns them
exactly as `finance review` would (IBAN -> iban_map source='confirmed'; merchant ->
learned.yaml), then re-categorises. Nothing else in Notion is ever read back.

Headless via the Notion REST API. The integration token is user-provisioned in the
NOTION_TOKEN env var (create an internal integration, share the hub page with it) — this
module never sees or stores the secret. Idempotent: each row carries its SQLite key
(dedup_key / file_hash / period_id) as a Notion property, so re-syncing updates in place.

    finance sync-notion            # create child DBs if needed, push the projection
    finance sync-notion --dry-run  # print exactly what would sync; touches nothing
    finance sync-notion --pull      # read Review Queue tags back into SQLite, re-categorise

Projection layers (mapped onto the hub's four sections):
    Transactions   (current + trailing N months detail)   -> "3 — Statements"
    Statements     (one row per ingested file)             -> "3 — Statements"
    Monthly Summary(per-period KPIs)                        -> "2 — This month"
    Review Queue   (needs_review groups; tag back via --pull) -> "2 — This month"
    Reference      (named IBAN directory)                  -> "4 — Reference vault"
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import yaml

from .config import Config, CONFIG_DIR
from . import db as dbm
from . import variance as var
from .periods import period_label, period_id_of
from datetime import date

NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1"
# hub_page_id + section_pages come from config/notion.yaml (gitignored real, or the
# committed .example placeholder). The hub is a database; its four rows are section
# PAGES, and child DBs are parented under them (you cannot nest a DB under a database).

# ---- database schemas (Notion property definitions) -------------------------
SCHEMAS = {
    "transactions": {
        "title": "Transactions",
        "props": {
            "Merchant": {"title": {}},
            "Date": {"date": {}},
            "Amount": {"number": {"format": "number"}},
            "Category": {"select": {}},
            "Account": {"select": {}},
            "Cardholder": {"select": {}},
            "Period": {"select": {}},
            "IBAN": {"rich_text": {}},
            "Internal": {"checkbox": {}},
            "Review": {"checkbox": {}},
            "dedup_key": {"rich_text": {}},
        },
        "key": "dedup_key",
    },
    "statements": {
        "title": "Statements",
        "props": {
            "File": {"title": {}},
            "Account": {"select": {}},
            "Period start": {"date": {}},
            "Period end": {"date": {}},
            "Total debit": {"number": {"format": "number"}},
            "Total credit": {"number": {"format": "number"}},
            "Txns": {"number": {"format": "number"}},
            "file_hash": {"rich_text": {}},
        },
        "key": "file_hash",
    },
    "monthly_summary": {
        "title": "Monthly Summary",
        "props": {
            "Period": {"title": {}},
            "period_id": {"rich_text": {}},
            "Income": {"number": {"format": "number"}},
            "Spend": {"number": {"format": "number"}},
            "Net": {"number": {"format": "number"}},
            "Savings %": {"number": {"format": "number"}},
            "Uncategorised %": {"number": {"format": "number"}},
        },
        "key": "period_id",
    },
    "review_queue": {
        "title": "Review Queue",
        "props": {
            "Counterparty": {"title": {}},
            "Type": {"select": {}},
            "Count": {"number": {"format": "number"}},
            "Amount": {"number": {"format": "number"}},
            "Payee": {"rich_text": {}},   # you fill (IBAN rows): the payee name to record
            "Category": {"select": {}},   # you fill: the category -> pulled back by --pull
            "rk": {"rich_text": {}},
        },
        "key": "rk",
    },
    "reference": {
        "title": "Reference — IBAN directory",
        "props": {
            "Payee": {"title": {}},
            "IBAN": {"rich_text": {}},
            "Category": {"select": {}},
            "Internal": {"checkbox": {}},
            "Source": {"select": {}},
        },
        "key": "IBAN",
    },
}


# ---- Notion value helpers ---------------------------------------------------
def _title(v):
    return {"title": [{"text": {"content": (v or "")[:2000]}}]}


def _text(v):
    return {"rich_text": [{"text": {"content": (str(v) if v is not None else "")[:2000]}}]}


def _num(v):
    return {"number": (round(float(v), 3) if v is not None else None)}


def _select(v):
    v = (str(v).strip() if v is not None else "")
    return {"select": {"name": v[:100]} if v else None}


def _date(v):
    return {"date": {"start": v} if v else None}


def _check(v):
    return {"checkbox": bool(v)}


# ---- projection builders (pure SQL -> list[dict]) ---------------------------
def _sync_periods(conn, cfg) -> list[str]:
    months = int(load_state().get("sync_window_months", 3))
    cur = period_id_of(date.today())
    latest = conn.execute("SELECT MAX(period_id) FROM transactions").fetchone()[0] or cur
    anchor = max(cur, latest)
    have = [r[0] for r in conn.execute(
        "SELECT DISTINCT period_id FROM transactions ORDER BY period_id DESC")]
    # keep the anchor + trailing `months` periods that have data
    keep = [p for p in have if p <= anchor][:months + 1]
    return keep


def transactions_rows(conn, cfg) -> list[dict]:
    periods = _sync_periods(conn, cfg)
    if not periods:
        return []
    q = ("SELECT * FROM transactions WHERE period_id IN (%s) ORDER BY txn_date"
         % ",".join("?" * len(periods)))
    out = []
    for r in conn.execute(q, periods):
        out.append({
            "Merchant": _title((r["raw_desc"] or "")[:80]),
            "Date": _date(r["txn_date"]),
            "Amount": _num(r["amount"]),
            "Category": _select(r["category"]),
            "Account": _select(r["source_account"]),
            "Cardholder": _select(r["cardholder"]),
            "Period": _select(r["period_id"]),
            "IBAN": _text(r["counterparty_iban"]),
            "Internal": _check(r["is_internal"]),
            "Review": _check(r["needs_review"]),
            "dedup_key": _text(r["dedup_key"]),
        })
    return out


def statements_rows(conn) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM source_files ORDER BY stmt_period_end"):
        out.append({
            "File": _title(r["filename"]),
            "Account": _select(r["source_account"]),
            "Period start": _date(r["stmt_period_start"]),
            "Period end": _date(r["stmt_period_end"]),
            "Txns": _num(r["n_txns"]),
            "file_hash": _text(r["file_hash"]),
        })
    return out


def monthly_summary_rows(conn) -> list[dict]:
    out = []
    for (pid,) in conn.execute(
            "SELECT DISTINCT period_id FROM transactions ORDER BY period_id"):
        s = var.period_summary(conn, pid)
        n = conn.execute("SELECT COUNT(*) FROM transactions WHERE period_id=?", (pid,)).fetchone()[0]
        u = conn.execute("SELECT COUNT(*) FROM transactions WHERE period_id=? AND category='Uncategorised'",
                         (pid,)).fetchone()[0]
        out.append({
            "Period": _title(period_label(pid)),
            "period_id": _text(pid),
            "Income": _num(s["income"]),
            "Spend": _num(s["spend"]),
            "Net": _num(s["net"]),
            "Savings %": _num(s["savings_rate"]),
            "Uncategorised %": _num(round(100 * u / n, 1) if n else 0),
        })
    return out


def review_rows(conn) -> list[dict]:
    out = []
    for r in conn.execute(
            """SELECT COALESCE(counterparty_iban, norm_desc) who, MAX(counterparty_iban) iban,
                      COUNT(*) n, -ROUND(SUM(CASE WHEN amount<0 THEN amount ELSE 0 END),3) amt
               FROM transactions WHERE needs_review=1 AND category='Uncategorised'
                 AND NOT (counterparty_iban IS NULL AND (norm_desc LIKE 'FOREIGN EXCHANGE%'
                          OR norm_desc LIKE 'VAT %' OR norm_desc LIKE 'NRT %'))
               GROUP BY who ORDER BY amt DESC LIMIT 100"""):
        out.append({
            "Counterparty": _title(r["who"][:80]),
            "Type": _select("IBAN" if r["iban"] else "Merchant"),
            "Count": _num(r["n"]),
            "Amount": _num(r["amt"]),
            "rk": _text(r["who"]),
        })
    return out


def reference_rows(conn) -> list[dict]:
    out = []
    for r in conn.execute(
            "SELECT * FROM iban_map WHERE category IS NOT NULL ORDER BY payee"):
        out.append({
            "Payee": _title(r["payee"] or r["iban"]),
            "IBAN": _text(r["iban"]),
            "Category": _select(r["category"]),
            "Internal": _check(r["is_internal"]),
            "Source": _select(r["source"]),
        })
    return out


LAYERS = [
    ("transactions", transactions_rows),
    ("statements", statements_rows),
    ("monthly_summary", monthly_summary_rows),
    ("review_queue", review_rows),
    ("reference", reference_rows),
]


# ---- state (child DB ids cached in config/notion.yaml) ----------------------
def _state_path() -> Path:
    return CONFIG_DIR / "notion.yaml"


def load_state() -> dict:
    p = _state_path()
    if not p.exists():
        p = CONFIG_DIR / "notion.example.yaml"   # sanitized fallback for a fresh clone
    data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    return data or {}


def save_state(state: dict) -> None:
    _state_path().write_text(
        "# Notion sync state (auto-managed). hub_page_id + created child-DB ids.\n"
        + yaml.safe_dump(state, sort_keys=False), encoding="utf-8")


# ---- HTTP client ------------------------------------------------------------
class NotionClient:
    def __init__(self, token: str):
        try:
            import truststore
            truststore.inject_into_ssl()   # TLS-inspecting proxy: use the OS trust store
        except Exception:
            pass
        import requests
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        })

    def _req(self, method, path, **kw):
        for attempt in range(5):
            r = self.s.request(method, f"{API}{path}", timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 1)))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Notion {method} {path} -> {r.status_code}: {r.text[:300]}")
            return r.json()
        raise RuntimeError("Notion API rate-limited after retries")

    def create_database(self, parent_page, title, props):
        return self._req("POST", "/databases", json={
            "parent": {"type": "page_id", "page_id": parent_page},
            "title": [{"text": {"content": title}}],
            "properties": props,
        })

    def find_by_key(self, db_id, key_prop, key_val):
        res = self._req("POST", f"/databases/{db_id}/query", json={
            "filter": {"property": key_prop, "rich_text": {"equals": key_val}},
            "page_size": 1,
        })
        results = res.get("results", [])
        return results[0]["id"] if results else None

    def create_page(self, db_id, props):
        return self._req("POST", "/pages", json={
            "parent": {"database_id": db_id}, "properties": props})

    def update_page(self, page_id, props):
        return self._req("PATCH", f"/pages/{page_id}", json={"properties": props})

    def query_all(self, db_id) -> list[dict]:
        """Every page in a database, following pagination."""
        out, cursor = [], None
        while True:
            body = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            res = self._req("POST", f"/databases/{db_id}/query", json=body)
            out.extend(res.get("results", []))
            if not res.get("has_more"):
                return out
            cursor = res.get("next_cursor")

    def archive_page(self, page_id):
        return self._req("PATCH", f"/pages/{page_id}", json={"archived": True})


def _key_value(row: dict, key_prop: str) -> str:
    val = row.get(key_prop)
    if not val:
        return ""
    if "rich_text" in val and val["rich_text"]:
        return val["rich_text"][0]["text"]["content"]
    if "title" in val and val["title"]:
        return val["title"][0]["text"]["content"]
    return ""


# ---- write-back (Notion Review Queue -> SQLite) -----------------------------
_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,}$")


def _looks_like_iban(s: str) -> bool:
    return bool(_IBAN_RE.match(s.strip()))


def _read_prop(page: dict, name: str) -> str:
    """Flatten one Notion page property (select / rich_text / title) to plain text."""
    p = (page.get("properties") or {}).get(name)
    if not p:
        return ""
    if p.get("select") is not None or "select" in p:
        sel = p.get("select")
        return (sel.get("name") if sel else "") or ""
    for kind in ("rich_text", "title"):
        if kind in p:
            return "".join(
                (x.get("plain_text") or x.get("text", {}).get("content", ""))
                for x in (p.get(kind) or []))
    return ""


def pull_review(cfg: Config, client=None, dry_run: bool = False, reload_cfg=None) -> int:
    """Read the Notion Review Queue back into SQLite. For each row you tagged with a valid
    Category, learn it the same way `finance review` does (IBAN -> iban_map confirmed;
    merchant -> learned.yaml), re-categorise, and archive the resolved Notion row.
    `reload_cfg` (default Config.load) supplies the post-learn config so freshly learned
    merchant keywords take effect on the re-categorise pass.
    """
    from . import review, categorise
    conn = dbm.connect(cfg.db_path)
    db_id = (load_state().get("databases") or {}).get("review_queue")
    if not db_id:
        conn.close()
        print("No Review Queue database in Notion yet. Run `finance sync-notion` first, "
              "tag rows there, then re-run with --pull.")
        return 1
    if client is None:
        token = os.environ.get("NOTION_TOKEN")
        if not token:
            conn.close()
            print("NOTION_TOKEN is not set — cannot read the Notion Review Queue.")
            return 1
        client = NotionClient(token)

    cats = review._valid_categories(conn)
    applied, skipped = [], 0
    for page in client.query_all(db_id):
        rk = _read_prop(page, "rk").strip()
        category = _read_prop(page, "Category").strip()
        if not rk or not category:
            continue                      # untouched row: nothing to learn
        if category not in cats:
            print(f"  ! {rk[:40]}: unknown category {category!r} — skipped")
            skipped += 1
            continue
        is_iban = _read_prop(page, "Type").strip().upper() == "IBAN" or _looks_like_iban(rk)
        payee = _read_prop(page, "Payee").strip() or None
        kind = "IBAN" if is_iban else "merchant"
        key = None if is_iban else review._key_from(rk)
        if not is_iban and not key:        # location/annotation line: not learnable
            skipped += 1
            continue
        if not dry_run:
            if is_iban:
                review._confirm_iban(conn, rk, payee, category)
            else:
                review._learn_keyword(key, category)
        applied.append((page.get("id"), rk, category, kind))

    if dry_run:
        print(f"--dry-run: would apply {len(applied)} tag(s), skip {skipped}. Nothing written.")
        for _, rk, category, kind in applied[:25]:
            print(f"    [{kind}] {rk[:44]} -> {category}")
        conn.close()
        return 0

    conn.commit()
    fresh = (reload_cfg or Config.load)()      # reload so learned.yaml keywords apply
    counts = categorise.categorise_all(conn, fresh)
    for page_id, _, _, _ in applied:           # tidy the queue: drop resolved rows
        try:
            client.archive_page(page_id)
        except Exception as e:                 # non-fatal: the learning already landed
            print(f"  (could not archive {page_id}: {e})")
    conn.close()
    pct = 100 * counts["uncategorised"] / counts["total"] if counts["total"] else 0
    print(f"Applied {len(applied)} Notion tag(s), skipped {skipped}. "
          f"Uncategorised now {counts['uncategorised']}/{counts['total']} ({pct:.1f}%).")
    return 0


# ---- orchestration ----------------------------------------------------------
def sync(cfg: Config, dry_run: bool = False) -> int:
    conn = dbm.connect(cfg.db_path)
    plans = [(name, builder(conn, cfg) if builder is transactions_rows else builder(conn))
             for name, builder in LAYERS]

    print("Notion sync projection (SQLite -> Notion, one-way):")
    for name, rows in plans:
        print(f"  {SCHEMAS[name]['title']:28} {len(rows):>5} rows")

    if dry_run:
        print("\n--dry-run: nothing sent. Sample of first Transactions row properties:")
        tx = dict(plans[0][1][0]) if plans[0][1] else {}
        for k in ("Merchant", "Date", "Amount", "Category", "Account", "Internal"):
            if k in tx:
                print(f"    {k}: {tx[k]}")
        conn.close()
        return 0

    token = os.environ.get("NOTION_TOKEN")
    if not token:
        conn.close()
        print("\nNOTION_TOKEN is not set. To enable the live sync:")
        print("  1. Create an internal integration at https://www.notion.so/my-integrations")
        print("  2. Share the '💰 Financial & Personal' hub page with that integration")
        print("  3. set NOTION_TOKEN=<secret>  and re-run  finance sync-notion")
        return 1

    state = load_state()
    hub = state.get("hub_page_id", "") or ""
    section_pages = state.get("section_pages", {}) or {}
    if not hub or hub.startswith("<"):
        conn.close()
        print("\nconfig/notion.yaml is missing your real hub_page_id. Copy "
              "config/notion.example.yaml to config/notion.yaml and fill in the hub + "
              "section page ids from your '💰 Financial & Personal' hub.")
        return 1

    client = NotionClient(token)
    dbs = state.setdefault("databases", {})

    for name, rows in plans:
        schema = SCHEMAS[name]
        if name not in dbs:
            parent = section_pages.get(name, hub)
            created = client.create_database(parent, schema["title"], schema["props"])
            dbs[name] = created["id"]
            save_state(state)
            print(f"  created DB '{schema['title']}' -> {dbs[name]}")
        db_id = dbs[name]
        key_prop = schema["key"]
        ins = upd = 0
        for row in rows:
            kv = _key_value(row, key_prop)
            existing = client.find_by_key(db_id, key_prop, kv) if kv else None
            if existing:
                client.update_page(existing, row); upd += 1
            else:
                client.create_page(db_id, row); ins += 1
        print(f"  {schema['title']:28} inserted {ins}, updated {upd}")

    save_state(state)
    conn.close()
    print("\nNotion hub synced. SQLite remains the source of truth.")
    return 0
