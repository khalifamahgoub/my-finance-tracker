"""Web review: form parsing + the apply-and-recategorise learning path.

The HTTP layer is thin; the logic that matters is parse_entries (form -> entries) and
apply_and_recategorise (entries -> iban_map / learned.yaml + re-categorise). Both are
exercised here with a temp DB and a tmp learned.yaml, mirroring the Notion-pull test.
"""
from __future__ import annotations

import re
import sqlite3

import yaml

from finance.config import Config, SCHEMA_SQL
from finance import webapp, review, theme, report_html


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


# ---- audit fixes: a11y, semantics, pagination, shared tokens ---------------
def _seed_many(dbp, n_merchants=5, n_ibans=2):
    """A temp DB with N distinct uncategorised merchants (descending amount, so
    _merchant_groups' ORDER BY amt DESC gives a deterministic sequence) plus N IBANs."""
    conn = sqlite3.connect(dbp)
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO periods(period_id,label,start_date,end_date) "
                "VALUES('2026-02','Feb 2026','2026-01-23','2026-02-22')")
    conn.executemany("INSERT INTO categories(name,section,kind,plan_category) VALUES(?,?,?,?)",
                     [("Groceries", "VARIABLE", "expense", "Groceries"),
                      ("Uncategorised", "REVIEW", "expense", None)])
    rows = []
    for i in range(n_merchants):
        rows.append((f"m{i}", "ila_cc", "2026-02-01", -(n_merchants - i), "x",
                     f"MERCHANT{i:02d} MANAMA 048", f"MERCHANT{i:02d} MANAMA 048", None))
    for i in range(n_ibans):
        rows.append((f"b{i}", "khaleeji", "2026-02-01", -(n_ibans - i), "x",
                     "OUTWARD FAWRI", "OUTWARD FAWRI", f"BH{i:02d}ABCD000000000000{i:02d}"))
    conn.executemany(
        """INSERT INTO transactions(dedup_key,source_account,txn_date,amount,currency,
           raw_desc,norm_desc,counterparty_iban,category,period_id,is_internal,needs_review)
           VALUES(?,?,?,?,?,?,?,?,'Uncategorised','2026-02',0,1)""", rows)
    conn.commit()
    conn.close()


def test_review_page_controls_have_aria_labels(tmp_path):
    dbp = tmp_path / "f.db"
    _seed_many(dbp)
    html = webapp.review_page(Config(db_path=str(dbp)))
    # every <select> and the payee <input> must carry an aria-label naming its row
    selects = re.findall(r"<select[^>]*>", html)
    assert selects and all('aria-label="' in s for s in selects)
    payee_inputs = re.findall(r'<input class="payee"[^>]*>', html)
    assert payee_inputs and all('aria-label="' in p for p in payee_inputs)
    assert "Category for MERCHANT00" in html
    assert "Payee name for IBAN BH00ABCD" in html


def test_review_page_tables_have_semantic_structure(tmp_path):
    dbp = tmp_path / "f.db"
    _seed_many(dbp)
    html = webapp.review_page(Config(db_path=str(dbp)))
    assert html.count("<caption>") == 2          # merchants table + IBAN table
    assert html.count("<thead>") == 2
    assert html.count('scope="col"') == 8           # 4 merchant cols + 4 iban cols


def test_review_page_has_skip_link_targeting_real_button(tmp_path):
    dbp = tmp_path / "f.db"
    _seed_many(dbp)
    html = webapp.review_page(Config(db_path=str(dbp)))
    assert 'href="#apply-btn"' in html
    assert 'id="apply-btn"' in html


def test_review_page_flash_is_a_live_region(tmp_path):
    dbp = tmp_path / "f.db"
    _seed_many(dbp)
    html = webapp.review_page(Config(db_path=str(dbp)), applied=3)
    assert 'role="status"' in html and 'aria-live="polite"' in html
    assert "Applied 3 tag(s)" in html


def test_review_page_paginates(tmp_path, monkeypatch):
    dbp = tmp_path / "f.db"
    _seed_many(dbp, n_merchants=7, n_ibans=0)
    monkeypatch.setattr(webapp, "PAGE_SIZE", 3)
    cfg = Config(db_path=str(dbp))

    page1 = webapp.review_page(cfg, page=1)
    page2 = webapp.review_page(cfg, page=2)
    page3 = webapp.review_page(cfg, page=3)

    assert "MERCHANT00" in page1 and "MERCHANT02" in page1 and "MERCHANT03" not in page1
    assert "MERCHANT03" in page2 and "MERCHANT05" in page2 and "MERCHANT00" not in page2
    assert "MERCHANT06" in page3
    assert "Page 1 of 3" in page1 and "Page 3 of 3" in page3
    assert "5 total" not in page1 and "7 total" in page1   # header shows the true total, not the page slice
    # out-of-range page clamps to the last page rather than erroring
    page_far = webapp.review_page(cfg, page=99)
    assert "Page 3 of 3" in page_far


def test_review_page_and_dashboard_share_one_token_source():
    # both surfaces literally import the same constant -> one palette, not two
    assert webapp.TOKENS_CSS is theme.TOKENS_CSS
    assert report_html.TOKENS_CSS is theme.TOKENS_CSS
