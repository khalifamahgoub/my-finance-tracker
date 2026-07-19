"""Write-back: Notion Review Queue tags -> SQLite learning, via a fake Notion client.

The live round-trip needs a NOTION_TOKEN and a real hub, so these tests drive
`notion_sync.pull_review` with canned Notion page JSON and assert the same learning the
CLI `finance review` produces: IBAN rows -> iban_map (source='confirmed'), merchant rows
-> learned.yaml, invalid categories skipped, and a --dry-run that touches nothing.
"""
from __future__ import annotations

import sqlite3

import yaml

from finance.config import Config, SCHEMA_SQL
from finance import notion_sync, review


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO periods(period_id,label,start_date,end_date) "
                 "VALUES('2026-02','Feb 2026','2026-01-23','2026-02-22')")
    conn.executemany("INSERT INTO categories(name,section,kind,plan_category) VALUES(?,?,?,?)",
                     [("Groceries", "VARIABLE", "expense", "Groceries"),
                      ("Dining", "VARIABLE", "expense", None),
                      ("Uncategorised", "REVIEW", "expense", None)])
    conn.executemany(
        """INSERT INTO transactions(dedup_key,source_account,txn_date,amount,currency,
           raw_desc,norm_desc,counterparty_iban,category,period_id,is_internal,needs_review)
           VALUES(?,?,?,?,'BHD',?,?,?,'Uncategorised','2026-02',0,1)""",
        [("k1", "khaleeji", "2026-02-05", -50, "x", "FAWRI TO CORNER LANDLORD",
          "BH12ABCD00000000000001"),
         ("k2", "ila_cc", "2026-02-06", -9, "x", "CORNER SHOP MANAMA 048", None)])
    conn.commit()
    conn.close()


def _page(pid, rk, typ, category, payee=""):
    return {"id": pid, "properties": {
        "rk": {"rich_text": [{"plain_text": rk, "text": {"content": rk}}]},
        "Type": {"select": {"name": typ} if typ else None},
        "Category": {"select": {"name": category} if category else None},
        "Payee": {"rich_text": ([{"plain_text": payee, "text": {"content": payee}}]
                                if payee else [])},
    }}


class _FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.archived = []

    def query_all(self, db_id):
        return list(self.pages)

    def archive_page(self, page_id):
        self.archived.append(page_id)


def _wire(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "CONFIG_DIR", tmp_path)               # learned.yaml -> tmp
    monkeypatch.setattr(notion_sync, "load_state",
                        lambda: {"databases": {"review_queue": "db1"}})


def test_pull_learns_iban_and_merchant_and_archives(tmp_path, monkeypatch):
    dbp = tmp_path / "f.db"
    _make_db(dbp)
    cfg = Config(db_path=str(dbp))
    _wire(tmp_path, monkeypatch)
    fake = _FakeClient([
        _page("p1", "BH12ABCD00000000000001", "IBAN", "Groceries", "Corner Landlord"),
        _page("p2", "CORNER SHOP MANAMA 048", "Merchant", "Dining"),
        _page("p3", "MYSTERY 048", "Merchant", "Nonexistent"),   # invalid category -> skip
        _page("p4", "UNTOUCHED 048", "Merchant", ""),            # not tagged -> ignore
    ])

    rc = notion_sync.pull_review(cfg, client=fake, reload_cfg=lambda: cfg)
    assert rc == 0

    conn = sqlite3.connect(dbp)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT payee,category,source,is_internal FROM iban_map "
                       "WHERE iban='BH12ABCD00000000000001'").fetchone()
    assert row["category"] == "Groceries" and row["source"] == "confirmed"
    assert row["payee"] == "Corner Landlord" and row["is_internal"] == 0
    # IBAN row re-categorises immediately (iban_map is read from the DB)
    t1 = conn.execute("SELECT category,needs_review FROM transactions WHERE dedup_key='k1'").fetchone()
    assert t1["category"] == "Groceries" and t1["needs_review"] == 0
    conn.close()

    learned = yaml.safe_load((tmp_path / "learned.yaml").read_text(encoding="utf-8"))
    assert "CORNER SHOP" in learned["keywords"]["Dining"]

    assert set(fake.archived) == {"p1", "p2"}       # only the two valid rows archived


def test_pull_dry_run_writes_nothing(tmp_path, monkeypatch):
    dbp = tmp_path / "f.db"
    _make_db(dbp)
    cfg = Config(db_path=str(dbp))
    _wire(tmp_path, monkeypatch)
    fake = _FakeClient([_page("p1", "BH12ABCD00000000000001", "IBAN", "Groceries", "X")])

    rc = notion_sync.pull_review(cfg, client=fake, dry_run=True, reload_cfg=lambda: cfg)
    assert rc == 0

    conn = sqlite3.connect(dbp)
    assert conn.execute("SELECT COUNT(*) FROM iban_map WHERE source='confirmed'").fetchone()[0] == 0
    conn.close()
    assert not (tmp_path / "learned.yaml").exists()
    assert fake.archived == []
