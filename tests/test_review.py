"""Keyword hygiene: _key_from must never emit a pure-location or FX/VAT/NRT keyword, and
the review queue must not surface FX-markup/VAT/NRT annotation lines (they inherit their
parent merchant's category). These guard the mass-mis-tagging class of bug where a learned
'MANAMA' -> Shopping keyword captured 500+ unrelated transactions.
"""
from __future__ import annotations

import sqlite3

from finance.config import SCHEMA_SQL
from finance import review


def test_key_from_rejects_location_and_annotation_lines():
    # pure location / mall -> nothing distinctive survives
    assert review._key_from("MANAMA") is None
    assert review._key_from("SEA FRONT") is None
    assert review._key_from("SEEF MALL 048") is None
    # FX-markup / VAT / NRT annotation lines
    assert review._key_from("FOREIGN EXCHANGE MARKUP CLAUDE AI") is None
    assert review._key_from("VAT DEBIT GOOGLE ONE") is None
    assert review._key_from("NRT PAYMENT OUTWARD FAWRI") is None


def test_key_from_keeps_the_distinctive_merchant_token():
    assert review._key_from("CORNER SHOP MANAMA 048") == "CORNER SHOP"
    assert review._key_from("CINEPOLIS SAAR 048") == "CINEPOLIS"        # geo 'SAAR' dropped
    assert review._key_from("KABBANI CAIRO EGY") == "KABBANI CAIRO"     # non-Bahrain geo kept
    assert review._key_from("668 CAFE MANAMA MA") == "CAFE MA"          # digits + 'MANAMA' dropped


def test_merchant_queue_excludes_fx_and_vat_lines():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executemany(
        """INSERT INTO transactions(dedup_key,source_account,txn_date,amount,currency,
           raw_desc,norm_desc,category,period_id,needs_review)
           VALUES(?,?,?,?,'BHD',?,?, 'Uncategorised','2026-02',1)""",
        [("a", "ila_cc", "2026-02-01", -10, "x", "KABBANI CAIRO EGY"),
         ("b", "ila_cc", "2026-02-01", -1, "x", "FOREIGN EXCHANGE MARKUP KABBANI"),
         ("c", "ila_cc", "2026-02-01", -1, "x", "VAT DEBIT KABBANI"),
         ("d", "ila_cc", "2026-02-01", -1, "x", "NRT PAYMENT KABBANI")])
    conn.commit()
    got = {r["norm_desc"] for r in review._merchant_groups(conn, 50)}
    assert got == {"KABBANI CAIRO EGY"}      # only the real merchant, no FX/VAT/NRT rows
    conn.close()
