"""Categorisation engine. Precedence (first match wins):
  1. own-account IBAN            -> Internal Transfer (net out)
  2. internal description keyword -> Internal Transfer (ila account: CC payment/Hassala/USD)
  3. CC 'Payment Received'        -> Internal Transfer (the payment leg)
  4. school IBAN                  -> Education (+sinking if >= term-fee threshold)
  5. remittance-hub IBAN          -> Recipient A / Recipient B (amount split)
  6. IBAN directory               -> mapped payee's category (REVIEW flag still flags)
  7. merchant keyword (rules.yaml)-> category
  8. fallback                     -> Uncategorised (+needs_review)

IBAN map is read from the DB so review-confirmed rows take effect immediately.
"""
from __future__ import annotations

import sqlite3

from .config import Config
from . import transfers as tf

_INTERNAL = {"category": "Internal Transfer", "is_internal": 1, "is_sinking": 0,
             "needs_review": 0}


class Categoriser:
    def __init__(self, conn: sqlite3.Connection, cfg: Config):
        self.cfg = cfg
        self.own = cfg.own_account_ibans
        self.school_iban = cfg.school_iban
        self.school_min = cfg.school_term_fee_min
        self.keyword_rules = cfg.keyword_rules
        self.internal_kws = [k.upper() for k in cfg.accounts.get("internal_desc_keywords", [])]
        self.remit_iban = (cfg.accounts.get("remittance_hub_iban") or "").upper() or None
        self.remit_split = cfg.accounts.get("remittance_split", {}) or {}
        self.iban_map = {r["iban"]: dict(r)
                         for r in conn.execute("SELECT * FROM iban_map")}

    def classify(self, source_account: str, iban: str | None, norm_desc: str,
                 amount: float, cardholder: str | None = None) -> dict:
        iban = (iban or "").upper() or None
        norm = norm_desc.upper()

        # 1-3. internal transfers (netting)
        if tf.is_own_internal(iban, self.own):
            return {**_INTERNAL, "rule_hit": "own_iban"}
        if "CREDIT CARD PAYMENT" in norm:   # paying a card (any account) is a transfer, not spend
            return {**_INTERNAL, "rule_hit": "cc_payment"}
        if tf.is_desc_internal(source_account, norm, self.internal_kws):
            return {**_INTERNAL, "rule_hit": "internal_desc"}
        if tf.is_cc_payment(source_account, norm):
            return {**_INTERNAL, "rule_hit": "cc_payment"}

        # 4. school sinking
        sc = tf.school_class(iban, amount, self.school_iban, self.school_min)
        if sc:
            cat, sinking = sc
            return {"category": cat, "is_internal": 0, "is_sinking": sinking,
                    "needs_review": 0, "rule_hit": "school_term" if sinking else "school_eca"}

        # 5. remittance hub split
        rc = tf.remittance_class(iban, amount, self.remit_iban, self.remit_split)
        if rc:
            return {"category": rc, "is_internal": 0, "is_sinking": 0,
                    "needs_review": 0, "rule_hit": "remit_split"}

        # 6. IBAN directory
        review = 0
        if iban and iban in self.iban_map:
            m = self.iban_map[iban]
            review = 1 if m.get("flag") == "REVIEW" else 0
            if m.get("is_internal"):
                return {**_INTERNAL, "rule_hit": f"iban:{m.get('payee') or iban}"}
            if m.get("category"):
                return {"category": m["category"], "is_internal": 0, "is_sinking": 0,
                        "needs_review": review, "rule_hit": f"iban:{m.get('payee') or iban}"}
            review = 1   # known IBAN but unnamed -> should be resolved

        # 7. merchant keyword
        for cat, kws in self.keyword_rules.items():
            if any(kw in norm for kw in kws):
                return {"category": cat, "is_internal": 0, "is_sinking": 0,
                        "needs_review": review, "rule_hit": f"kw:{cat}"}

        # 8. fallback
        return {"category": "Uncategorised", "is_internal": 0, "is_sinking": 0,
                "needs_review": 1, "rule_hit": None}


def categorise_all(conn: sqlite3.Connection, cfg: Config) -> dict:
    """(Re)categorise every transaction. Idempotent; config/iban_map edits propagate."""
    cat = Categoriser(conn, cfg)
    rows = conn.execute(
        "SELECT txn_id, source_account, counterparty_iban, norm_desc, amount, cardholder "
        "FROM transactions").fetchall()
    counts = {"internal": 0, "sinking": 0, "review": 0, "uncategorised": 0}
    for r in rows:
        c = cat.classify(r["source_account"], r["counterparty_iban"], r["norm_desc"],
                         r["amount"], r["cardholder"])
        conn.execute(
            "UPDATE transactions SET category=?, is_internal=?, is_sinking=?, "
            "needs_review=?, rule_hit=? WHERE txn_id=?",
            (c["category"], c["is_internal"], c["is_sinking"], c["needs_review"],
             c["rule_hit"], r["txn_id"]))
        counts["internal"] += c["is_internal"]
        counts["sinking"] += c["is_sinking"]
        counts["review"] += c["needs_review"]
        if c["category"] == "Uncategorised":
            counts["uncategorised"] += 1
    conn.commit()
    counts["total"] = len(rows)
    return counts
