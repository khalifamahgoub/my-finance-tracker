"""SQLite access: connect, bootstrap the schema, seed reference data, and the
idempotent transaction upsert. Plain SQL, no ORM (PRD: a thin layer is enough).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import Config, SCHEMA_SQL


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def bootstrap(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    conn.commit()


def seed_categories(conn: sqlite3.Connection, cfg: Config) -> int:
    rows = (cfg.categories or {}).get("categories", {}) or {}
    n = 0
    for order, (name, meta) in enumerate(rows.items()):
        meta = meta or {}
        conn.execute(
            """INSERT INTO categories(name, section, kind, plan_category, sort_order)
               VALUES(?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 section=excluded.section, kind=excluded.kind,
                 plan_category=excluded.plan_category, sort_order=excluded.sort_order""",
            (name, meta.get("section"), meta.get("kind", "expense"),
             meta.get("plan"), order),
        )
        n += 1
    conn.commit()
    return n


def seed_iban_map(conn: sqlite3.Connection, cfg: Config) -> int:
    rows = (cfg.iban_map or {}).get("ibans", {}) or {}
    n = 0
    for iban, meta in rows.items():
        meta = meta or {}
        conn.execute(
            """INSERT INTO iban_map(iban, payee, category, is_internal, flag,
                                    typical_min, typical_max, source, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(iban) DO UPDATE SET
                 payee=excluded.payee, category=excluded.category,
                 is_internal=excluded.is_internal, flag=excluded.flag,
                 typical_min=excluded.typical_min, typical_max=excluded.typical_max,
                 source=excluded.source, updated_at=excluded.updated_at
               WHERE iban_map.source IS NOT 'confirmed'""",  # never clobber learned rows
            (iban.strip().upper(), meta.get("payee"), meta.get("category"),
             int(meta.get("is_internal", 0)), meta.get("flag"),
             meta.get("typical_min"), meta.get("typical_max"),
             meta.get("source", "seed"), _now()),
        )
        n += 1
    conn.commit()
    return n


def seed_sinking_funds(conn: sqlite3.Connection, cfg: Config) -> int:
    funds = cfg.sinking_funds or {}
    n = 0
    for fund_id, meta in funds.items():
        meta = meta or {}
        due = (meta.get("due_periods") or [None])
        conn.execute(
            """INSERT INTO sinking_funds(fund_id, label, target_amount, due_period, iban, notes)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(fund_id) DO UPDATE SET
                 label=excluded.label, target_amount=excluded.target_amount,
                 due_period=excluded.due_period, iban=excluded.iban, notes=excluded.notes""",
            (fund_id, meta.get("label"), meta.get("target_amount"),
             due[0] if isinstance(due, list) else due, meta.get("iban"), meta.get("notes")),
        )
        n += 1
    conn.commit()
    return n


def ensure_period(conn: sqlite3.Connection, period_row: dict[str, str]) -> None:
    conn.execute(
        """INSERT INTO periods(period_id, label, start_date, end_date)
           VALUES(:period_id, :label, :start_date, :end_date)
           ON CONFLICT(period_id) DO NOTHING""",
        period_row,
    )


TXN_COLUMNS = [
    "dedup_key", "source_account", "cardholder", "txn_date", "amount", "currency",
    "raw_desc", "norm_desc", "counterparty_iban", "balance", "period_id", "category",
    "is_internal", "is_sinking", "needs_review", "rule_hit", "fx_currency", "fx_amount",
    "source_file", "created_at",
]


def upsert_transaction(conn: sqlite3.Connection, txn: dict[str, Any]) -> bool:
    """Insert a transaction; return True if newly inserted, False if it already existed
    (idempotency via the UNIQUE dedup_key)."""
    txn = {**txn}
    txn.setdefault("created_at", _now())
    for flag in ("is_internal", "is_sinking", "needs_review"):
        txn[flag] = int(txn.get(flag) or 0)   # NOT NULL columns
    cols = ", ".join(TXN_COLUMNS)
    placeholders = ", ".join(f":{c}" for c in TXN_COLUMNS)
    cur = conn.execute(
        f"INSERT INTO transactions({cols}) VALUES({placeholders}) "
        f"ON CONFLICT(dedup_key) DO NOTHING",
        {c: txn.get(c) for c in TXN_COLUMNS},
    )
    return cur.rowcount > 0


def file_already_ingested(conn: sqlite3.Connection, file_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM source_files WHERE file_hash = ?", (file_hash,)
    ).fetchone()
    return row is not None


def record_source_file(conn: sqlite3.Connection, rec: dict[str, Any]) -> None:
    conn.execute(
        """INSERT INTO source_files(file_hash, filename, source_account, stmt_period_start,
                                    stmt_period_end, n_txns, ingested_at, archived_path)
           VALUES(:file_hash,:filename,:source_account,:stmt_period_start,
                  :stmt_period_end,:n_txns,:ingested_at,:archived_path)
           ON CONFLICT(file_hash) DO UPDATE SET
             archived_path=excluded.archived_path, n_txns=excluded.n_txns""",
        rec,
    )


def table_names(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


def init_db(cfg: Config) -> sqlite3.Connection:
    """Connect, create schema, seed reference data. Idempotent."""
    conn = connect(cfg.db_path)
    bootstrap(conn)
    seed_categories(conn, cfg)
    seed_iban_map(conn, cfg)
    seed_sinking_funds(conn, cfg)
    return conn
