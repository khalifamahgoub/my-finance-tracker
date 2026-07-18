"""Ingest pipeline: scan inbox -> detect -> parse -> assign period -> dedup-insert ->
archive. Idempotent at two levels: a file-hash skip (source_files) and the per-row
UNIQUE dedup_key, so re-running changes nothing.

Categorisation/netting is layered on in Phase 2 (categorise.py, transfers.py); here
rows land with category=NULL and are enriched afterward.
"""
from __future__ import annotations

import hashlib
import shutil
from datetime import date
from pathlib import Path

import fitz

from .config import Config
from . import db as dbm
from .normalise import normalise_desc, dedup_key
from .periods import period_id_of, period_row
from .parsers import detect_parser


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    h.update(path.read_bytes())
    return h.hexdigest()


def _inbox_pdfs(cfg: Config) -> list[Path]:
    return sorted(p for p in cfg.inbox.glob("*.pdf") if p.is_file())


def _row_from_txn(source_account: str, t, filename: str, occ: int = 0) -> dict:
    norm = normalise_desc(t.raw_desc, t.counterparty_iban)
    pid = period_id_of(date.fromisoformat(t.txn_date))
    return {
        "dedup_key": dedup_key(source_account, t.txn_date, t.amount, norm, occ),
        "source_account": source_account,
        "cardholder": t.cardholder,
        "txn_date": t.txn_date,
        "amount": t.amount,
        "currency": t.currency,
        "raw_desc": t.raw_desc,
        "norm_desc": norm,
        "counterparty_iban": t.counterparty_iban,
        "balance": t.balance,
        "period_id": pid,
        "fx_currency": t.fx_currency,
        "fx_amount": t.fx_amount,
        "source_file": filename,
    }


def _archive(cfg: Config, path: Path, month: str | None) -> str:
    dest_dir = cfg.archive / (month or "unknown")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    shutil.move(str(path), str(dest))
    return str(dest)


def run(cfg: Config, sync: bool = False, narrate: bool = False, archive: bool = True) -> int:
    from . import categorise

    conn = dbm.connect(cfg.db_path)
    files = _inbox_pdfs(cfg)
    if not files:
        print(f"inbox empty ({cfg.inbox}) - re-categorising existing transactions.")

    tot_ins = tot_dedup = tot_skip = 0
    by_source: dict[str, int] = {}
    unmatched: list[str] = []

    for path in files:
        fh = _sha1(path)
        if dbm.file_already_ingested(conn, fh):
            tot_skip += 1
            if archive:
                prior = conn.execute(
                    "SELECT stmt_period_end FROM source_files WHERE file_hash=?",
                    (fh,)).fetchone()
                _archive(cfg, path, (prior[0] or "")[:7] if prior and prior[0] else "unknown")
            continue
        doc = fitz.open(path)
        parser = detect_parser(path, doc, cfg)
        if parser is None:
            unmatched.append(path.name)
            continue
        st = parser.parse(path, doc)
        doc.close()   # release the file handle before archiving (Windows lock)

        ins = ded = 0
        seen: dict[tuple, int] = {}
        for t in st.txns:
            norm = normalise_desc(t.raw_desc, t.counterparty_iban)
            key = (t.txn_date, round(t.amount, 3), norm)
            occ = seen.get(key, 0)
            seen[key] = occ + 1
            row = _row_from_txn(st.source_account, t, path.name, occ)
            dbm.ensure_period(conn, period_row(row["period_id"]))
            if dbm.upsert_transaction(conn, row):
                ins += 1
            else:
                ded += 1
        by_source[st.source_account] = by_source.get(st.source_account, 0) + ins
        tot_ins += ins
        tot_dedup += ded

        month = (st.period_end or st.period_start or "")[:7] or "unknown"
        archived_path = _archive(cfg, path, month) if archive else None
        dbm.record_source_file(conn, {
            "file_hash": fh, "filename": path.name, "source_account": st.source_account,
            "stmt_period_start": st.period_start, "stmt_period_end": st.period_end,
            "n_txns": len(st.txns), "ingested_at": dbm._now(), "archived_path": archived_path,
        })
        conn.commit()

    # Plan workbooks (.xlsx) in the inbox -> plan_lines.
    from . import plan as planmod
    plan_lines = 0
    for xlsx in sorted(cfg.inbox.glob("*.xlsx")):
        n = planmod.ingest_plan(conn, cfg, xlsx)
        plan_lines += n
        if archive:
            _archive(cfg, xlsx, "plans")
    if plan_lines:
        print(f"  plan lines loaded: {plan_lines}")

    cat_counts = categorise.categorise_all(conn, cfg)
    _summary(conn, files, tot_ins, tot_dedup, tot_skip, by_source, unmatched, cat_counts)
    conn.close()

    from . import report_html
    html_path, period_id = report_html.generate(cfg)
    print(f"\nDashboard: {html_path}  (period {period_id})")

    if sync:
        # Best-effort: local data is already saved; a sync failure never fails the run.
        from . import notion_sync
        print()
        try:
            notion_sync.sync(cfg, dry_run=False)
        except Exception as e:
            print(f"Notion sync failed (local data is safe): {e}")

    if narrate:
        # Opt-in AI narrative (best-effort; never fails the run). narrate() prints its
        # own guidance if ANTHROPIC_API_KEY is unset.
        from . import narrate as narr
        print()
        try:
            narr.narrate(cfg, period=None)
        except Exception as e:
            print(f"narrate skipped: {e}")
    return 0


def _summary(conn, files, ins, dedup, skip, by_source, unmatched, cat=None) -> None:
    print(f"\nProcessed {len(files)} file(s):")
    print(f"  inserted: {ins}   deduped: {dedup}   file-skipped (already ingested): {skip}")
    if by_source:
        print("  by source: " + ", ".join(f"{k}={v}" for k, v in sorted(by_source.items())))
    if unmatched:
        print(f"  UNMATCHED (no parser): {', '.join(unmatched)}")
    total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    periods = conn.execute("SELECT COUNT(*) FROM periods").fetchone()[0]
    print(f"  DB now holds {total} transactions across {periods} periods.")
    if cat:
        pct = 100 * cat["uncategorised"] / cat["total"] if cat["total"] else 0
        print(f"  categorised: {cat['internal']} internal, {cat['sinking']} sinking, "
              f"{cat['review']} need review, {cat['uncategorised']} uncategorised ({pct:.1f}%).")
