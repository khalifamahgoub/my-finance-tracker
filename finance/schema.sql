-- Personal Finance Tracker — SQLite schema (system of record).
-- Money stored as REAL rounded to 3 dp (BHD fils). Reconcile at 0.001 tolerance.
-- Idempotent bootstrap: every table is CREATE ... IF NOT EXISTS.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Financial periods run 23rd -> 22nd, named by the month containing the 22nd.
CREATE TABLE IF NOT EXISTS periods (
    period_id  TEXT PRIMARY KEY,          -- 'YYYY-MM' of the month holding the 22nd, e.g. '2026-02'
    label      TEXT NOT NULL,             -- 'Feb 2026'
    start_date TEXT NOT NULL,             -- ISO 'YYYY-MM-DD' (the 23rd of the prior month)
    end_date   TEXT NOT NULL,             -- ISO 'YYYY-MM-DD' (the 22nd)
    UNIQUE (start_date, end_date)
);

-- Canonical category taxonomy (seeded from config/categories.yaml).
CREATE TABLE IF NOT EXISTS categories (
    name          TEXT PRIMARY KEY,
    section       TEXT,                   -- INCOME | FIXED | VARIABLE | INTERNAL | OTHER | REVIEW
    kind          TEXT NOT NULL DEFAULT 'expense',  -- income | expense | transfer | sinking
    plan_category TEXT,                   -- plan line this rolls up to (null = unbudgeted)
    sort_order    INTEGER
);

-- IBAN directory (seeded from config/iban_map.yaml; learned rows appended, source='confirmed').
CREATE TABLE IF NOT EXISTS iban_map (
    iban        TEXT PRIMARY KEY,         -- normalised: upper, no spaces
    payee       TEXT,
    category    TEXT REFERENCES categories(name),
    is_internal INTEGER NOT NULL DEFAULT 0,  -- 1 = own account -> net out
    flag        TEXT,                     -- 'REVIEW' forces manual review on every hit
    typical_min REAL,
    typical_max REAL,
    source      TEXT,                     -- 'prd' | 'ibans_to_tag' | 'confirmed'
    notes       TEXT,
    updated_at  TEXT
);

-- Transactions (one row per statement line; FX markup/VAT are their own rows).
CREATE TABLE IF NOT EXISTS transactions (
    txn_id            INTEGER PRIMARY KEY,
    dedup_key         TEXT NOT NULL UNIQUE,   -- sha1(source_account|date|amount|norm_desc), scoped per source
    source_account    TEXT NOT NULL,          -- 'khaleeji' | 'ila_cc' | 'ila_account'
    cardholder        TEXT,                   -- 'primary' | 'supplementary' | NULL
    txn_date          TEXT NOT NULL,          -- ISO 'YYYY-MM-DD'
    amount            REAL NOT NULL,          -- SIGNED: negative = outflow/spend, positive = inflow
    currency          TEXT NOT NULL DEFAULT 'BHD',
    raw_desc          TEXT NOT NULL,
    norm_desc         TEXT NOT NULL,
    counterparty_iban TEXT,
    balance           REAL,                   -- running balance if the statement supplies it
    period_id         TEXT REFERENCES periods(period_id),
    category          TEXT REFERENCES categories(name),
    is_internal       INTEGER NOT NULL DEFAULT 0,
    is_sinking        INTEGER NOT NULL DEFAULT 0,
    needs_review      INTEGER NOT NULL DEFAULT 0,
    rule_hit          TEXT,                   -- which iban/keyword rule matched (audit)
    fx_currency       TEXT,
    fx_amount         REAL,
    source_file       TEXT,
    created_at        TEXT
);
CREATE INDEX IF NOT EXISTS ix_txn_period ON transactions(period_id);
CREATE INDEX IF NOT EXISTS ix_txn_cat    ON transactions(category);
CREATE INDEX IF NOT EXISTS ix_txn_review ON transactions(needs_review);
CREATE INDEX IF NOT EXISTS ix_txn_iban   ON transactions(counterparty_iban);
CREATE INDEX IF NOT EXISTS ix_txn_source ON transactions(source_account);

-- Plan lines (melted from the annual '2026 Cash Flow' sheet); native plan labels kept.
CREATE TABLE IF NOT EXISTS plan_lines (
    plan_line_id    INTEGER PRIMARY KEY,
    period_id       TEXT REFERENCES periods(period_id),
    category        TEXT NOT NULL,          -- the plan's native label, e.g. 'Variable (Food, Fuel, etc.)'
    section         TEXT,                   -- INCOME | FIXED EXPENSES | ...
    planned_amount  REAL NOT NULL,
    mechanism       TEXT,                   -- raw cell: 'Credit Card' | 'Khaleeji Account' | an IBAN
    mechanism_iban  TEXT,
    source_workbook TEXT,
    UNIQUE (period_id, category)
);

-- Sinking / savings funds (current balance computed from is_sinking transactions).
CREATE TABLE IF NOT EXISTS sinking_funds (
    fund_id              TEXT PRIMARY KEY,   -- 'school_fees' | 'emergency'
    label                TEXT,
    target_amount        REAL,
    due_period           TEXT,               -- next due period_id
    monthly_contribution REAL,
    iban                 TEXT,
    notes                TEXT
);

-- File-level provenance for fast idempotent skip on re-run.
CREATE TABLE IF NOT EXISTS source_files (
    file_hash         TEXT PRIMARY KEY,      -- sha1 of file bytes
    filename          TEXT,
    source_account    TEXT,
    stmt_period_start TEXT,
    stmt_period_end   TEXT,
    n_txns            INTEGER,
    ingested_at       TEXT,
    archived_path     TEXT
);
