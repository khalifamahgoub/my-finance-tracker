"""Interactive review: resolve transactions flagged needs_review, and LEARN from each
answer so accuracy compounds. Named IBANs -> iban_map (source='confirmed', never
clobbered by re-seed). Named merchants -> config/learned.yaml (merged by config, so the
hand-written rules.yaml keeps its comments). Re-categorises at the end.

Input format per prompt: for an IBAN group  ->  "Payee | Category"  (or just "Category")
for a merchant group -> "Category". Blank line skips. Ctrl-D / EOF ends early.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import yaml

from .config import Config, CONFIG_DIR
from . import db as dbm


def _valid_categories(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM categories")}


def _iban_groups(conn, limit):
    return conn.execute(
        """SELECT counterparty_iban AS iban, COUNT(*) AS n,
                  -ROUND(SUM(CASE WHEN amount<0 THEN amount ELSE 0 END),3) AS outflow,
                  MIN(txn_date) AS first, MAX(txn_date) AS last
           FROM transactions WHERE needs_review=1 AND counterparty_iban IS NOT NULL
           GROUP BY counterparty_iban ORDER BY n DESC, outflow DESC LIMIT ?""",
        (limit,)).fetchall()


def _merchant_groups(conn, limit):
    # FX-markup / VAT / NRT lines are excluded: they carry their parent merchant's name
    # and inherit its category, so they must not be tagged (or learned) on their own.
    return conn.execute(
        """SELECT norm_desc, COUNT(*) AS n, -ROUND(SUM(amount),3) AS amt
           FROM transactions WHERE needs_review=1 AND counterparty_iban IS NULL
             AND category='Uncategorised'
             AND norm_desc NOT LIKE 'FOREIGN EXCHANGE%'
             AND norm_desc NOT LIKE 'VAT %'
             AND norm_desc NOT LIKE 'NRT %'
           GROUP BY norm_desc ORDER BY n DESC, amt DESC LIMIT ?""", (limit,)).fetchall()


def _confirm_iban(conn, iban, payee, category):
    conn.execute(
        """INSERT INTO iban_map(iban, payee, category, is_internal, source, updated_at)
           VALUES(?,?,?,0,'confirmed',?)
           ON CONFLICT(iban) DO UPDATE SET payee=excluded.payee, category=excluded.category,
             source='confirmed', updated_at=excluded.updated_at""",
        (iban, payee, category, datetime.now(timezone.utc).isoformat(timespec="seconds")))


def _learn_keyword(keyword: str | None, category: str):
    if not keyword:                       # non-distinctive / annotation line: nothing to learn
        return
    path = CONFIG_DIR / "learned.yaml"
    data = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    kws = data.setdefault("keywords", {})
    kws.setdefault(category, [])
    if keyword not in kws[category]:
        kws[category].append(keyword)
    path.write_text(
        "# Review-confirmed keywords (auto-appended). Merged with rules.yaml at load.\n"
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def run_review(cfg: Config, limit: int = 25, stream=None) -> int:
    stream = stream or sys.stdin
    conn = dbm.connect(cfg.db_path)
    cats = _valid_categories(conn)

    iban_groups = _iban_groups(conn, limit)
    merch_groups = _merchant_groups(conn, limit)
    if not iban_groups and not merch_groups:
        print("Nothing needs review. 🎉")
        conn.close()
        return 0

    print(f"Review queue: {len(iban_groups)} IBAN group(s), {len(merch_groups)} merchant group(s).")
    print("Answer 'Payee | Category' for IBANs, 'Category' for merchants; blank skips.\n")

    resolved = 0
    for g in iban_groups:
        print(f"IBAN {g['iban']}  ({g['n']} txns, BHD {g['outflow']} out, {g['first']}..{g['last']})")
        line = _read(stream)
        if line is None:
            break
        if not line.strip():
            continue
        payee, category = _split(line)
        if category and category not in cats:
            print(f"  ! unknown category {category!r} - skipped"); continue
        _confirm_iban(conn, g["iban"], payee, category or "Uncategorised")
        resolved += 1

    for g in merch_groups:
        print(f"MERCHANT {g['norm_desc'][:48]}  ({g['n']} txns, BHD {g['amt']})")
        line = _read(stream)
        if line is None:
            break
        if not line.strip():
            continue
        category = line.strip()
        if category not in cats:
            print(f"  ! unknown category {category!r} - skipped"); continue
        key = _key_from(g["norm_desc"])
        if not key:
            print("  (location/annotation line — inherits parent; skipped)"); continue
        _learn_keyword(key, category)
        resolved += 1

    conn.commit()
    from . import categorise
    cfg2 = Config.load()   # reload so learned.yaml keywords take effect
    counts = categorise.categorise_all(conn, cfg2)
    conn.close()
    pct = 100 * counts["uncategorised"] / counts["total"] if counts["total"] else 0
    print(f"\nResolved {resolved} group(s). Uncategorised now "
          f"{counts['uncategorised']}/{counts['total']} ({pct:.1f}%).")
    return 0


def _read(stream):
    line = stream.readline()
    return None if line == "" else line.rstrip("\n")   # "" == EOF


def _split(line: str):
    if "|" in line:
        a, b = line.split("|", 1)
        return a.strip(), b.strip()
    return None, line.strip()


# Bahrain area names + generic geo words that must never become a keyword on their own —
# they appear across unrelated merchants and cause mass mis-tagging (e.g. MANAMA -> Shopping
# matched 500+ transactions). Kept out of learned keywords; a distinctive merchant token
# must survive alongside them.
_GEO = {
    "MANAMA", "SEEF", "SAAR", "JANABIYAH", "ALJANABIYAH", "MUHARRAQ", "HIDD", "RIFFA",
    "BUDAIYA", "SANABIS", "JUFFAIR", "ADLIYA", "ZALLAQ", "BUSAITEEN", "SALMABAD", "TUBLI",
    "GUDAIBIYA", "MARKH", "ARAD", "GALALI", "DIRAZ", "BILAD", "SALMANIYA", "MAHOOZ", "SANAD",
    "WADI", "BUQUWAH", "BAHRAIN", "BHR", "DISTRICT", "TOWN", "MALL", "FRONT", "SEA",
}
# FX-markup / VAT / NRT lines prefix every foreign purchase and inherit the parent
# merchant's category — never learnable as keywords.
_NONLEARN_PREFIX = ("FOREIGN EXCHANGE", "VAT ", "NRT ")


def _key_from(norm_desc: str) -> str | None:
    """A distinctive keyword from a merchant description: the first two significant tokens,
    dropping digits and geo/location words. Returns None when nothing distinctive remains
    (a pure-location string) or the line is an FX-markup/VAT/NRT annotation — those must
    never become keywords."""
    if norm_desc.upper().strip().startswith(_NONLEARN_PREFIX):
        return None
    toks = [t for t in norm_desc.split()
            if len(t) > 1 and not t.isdigit() and t.upper() not in _GEO]
    key = " ".join(toks[:2]).strip()
    return key or None
