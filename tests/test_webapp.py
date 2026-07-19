"""Web review: form parsing + the apply-and-recategorise learning path.

The HTTP layer is thin; the logic that matters is parse_entries (form -> entries) and
apply_and_recategorise (entries -> iban_map / learned.yaml + re-categorise). Both are
exercised here with a temp DB and a tmp learned.yaml, mirroring the Notion-pull test.
"""
from __future__ import annotations

import sqlite3

import yaml

from finance.config import Config, SCHEMA_SQL
from finance import webapp, review


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


def test_parse_entries_indexes_by_rk():
    form = {
        "rk_0": ["CORNER SHOP MANAMA 048"], "kind_0": ["merchant"], "cat_0": ["Dining"],
        "rk_1": ["BH12ABCD00000000000001"], "kind_1": ["iban"], "cat_1": ["Groceries"],
        "payee_1": ["Corner Landlord"],
        "rk_2": ["SKIP ME 048"], "kind_2": ["merchant"], "cat_2": [""],   # blank -> kept, empty cat
    }
    entries = webapp.parse_entries(form)
    assert entries[0] == {"rk": "CORNER SHOP MANAMA 048", "is_iban": False,
                          "category": "Dining", "payee": ""}
    assert entries[1]["is_iban"] is True and entries[1]["payee"] == "Corner Landlord"
    assert entries[2]["category"] == ""      # blank category preserved (skipped at apply)


def test_apply_learns_and_recategorises(tmp_path, monkeypatch):
    dbp = tmp_path / "f.db"
    _make_db(dbp)
    cfg = Config(db_path=str(dbp))
    monkeypatch.setattr(review, "CONFIG_DIR", tmp_path)      # _learn_keyword writes here
    monkeypatch.setattr("finance.config.CONFIG_DIR", tmp_path)  # keyword_rules reads here (hermetic)
    entries = [
        {"rk": "BH12ABCD00000000000001", "is_iban": True, "category": "Groceries", "payee": "Corner Landlord"},
        {"rk": "CORNER SHOP MANAMA 048", "is_iban": False, "category": "Dining", "payee": ""},
        {"rk": "MYSTERY 048", "is_iban": False, "category": "Nonexistent", "payee": ""},  # invalid -> skip
        {"rk": "UNTOUCHED 048", "is_iban": False, "category": "", "payee": ""},           # blank -> skip
    ]
    applied, counts = webapp.apply_and_recategorise(cfg, entries, reload_cfg=lambda: cfg)
    assert applied == 2

    conn = sqlite3.connect(dbp)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT category,source,payee FROM iban_map "
                       "WHERE iban='BH12ABCD00000000000001'").fetchone()
    assert row["category"] == "Groceries" and row["source"] == "confirmed" and row["payee"] == "Corner Landlord"
    t1 = conn.execute("SELECT category,needs_review FROM transactions WHERE dedup_key='k1'").fetchone()
    assert t1["category"] == "Groceries" and t1["needs_review"] == 0   # DB iban_map drives reclassification
    conn.close()

    learned = yaml.safe_load((tmp_path / "learned.yaml").read_text(encoding="utf-8"))
    assert "CORNER SHOP" in learned["keywords"]["Dining"]
