"""Description normalisation + dedup key + IBAN extraction.

The dedup key makes re-running the pipeline on an already-processed (or overlapping)
statement a no-op: same source + date + amount + normalised description -> same key ->
INSERT ... ON CONFLICT DO NOTHING. Pure functions; unit-tested in tests/test_normalise.py.
"""
from __future__ import annotations

import hashlib
import re

# Bahrain IBAN: BH + 2 check digits + 4-letter bank code + 14 alphanumerics = 22 chars.
IBAN_RE = re.compile(r"\bBH\d{2}[A-Z]{4}[A-Z0-9]{14}\b")

# Volatile / reference tokens stripped for clean merchant grouping.
_BP_REF = re.compile(r"\bBP\d{6,}[A-Z0-9]*\b")     # Khaleeji BenefitPay serial
_HASH_SERIAL = re.compile(r"#\s*\d+")               # '# 62196003'
_LONG_DIGITS = re.compile(r"\b\d{5,}\b")            # switch acct / reference numbers
_NON_ALNUM = re.compile(r"[^A-Z0-9 ]+")
_WS = re.compile(r"\s+")


def extract_iban(text: str) -> str | None:
    """First Bahrain IBAN in the text, upper-cased, or None. Tolerates spaced IBANs."""
    if not text:
        return None
    up = text.upper()
    m = IBAN_RE.search(up) or IBAN_RE.search(up.replace(" ", ""))
    return m.group(0) if m else None


def normalise_desc(raw: str, counterparty_iban: str | None = None) -> str:
    """Uppercase, strip volatile refs, collapse punctuation/space. Append the IBAN so
    transfers to different beneficiaries never collide on the dedup key."""
    s = (raw or "").upper()
    s = _BP_REF.sub(" ", s)
    s = _HASH_SERIAL.sub(" ", s)
    s = _LONG_DIGITS.sub(" ", s)
    s = _NON_ALNUM.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    if counterparty_iban:
        s = f"{s} {counterparty_iban.strip().upper()}".strip()
    return s


def dedup_key(source_account: str, txn_date: str, amount: float, norm_desc: str,
              occ: int = 0) -> str:
    """Stable per-source identity for a transaction line. `occ` is the occurrence index
    of this (date, amount, norm_desc) within its statement, so two genuinely identical
    purchases in one statement stay distinct while a re-downloaded statement (which
    reproduces the same occurrence order) still collapses to the same keys."""
    raw = f"{source_account}|{txn_date}|{amount:.3f}|{norm_desc}|{occ}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()
