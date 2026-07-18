"""Parser output contracts. Parsers return these; ingest turns them into DB rows,
so a new parser (or the OCR fallback) slots in without touching downstream code.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParsedTxn:
    txn_date: str                       # ISO 'YYYY-MM-DD'
    amount: float                       # SIGNED: negative = outflow/spend, positive = inflow
    raw_desc: str
    balance: float | None = None        # running balance if the statement supplies it
    counterparty_iban: str | None = None
    cardholder: str | None = None       # 'primary' | 'supplementary'
    currency: str = "BHD"
    fx_currency: str | None = None
    fx_amount: float | None = None
    section: str | None = None          # parser hint, e.g. 'payments' (CC) -> internal


@dataclass
class ParsedStatement:
    source_account: str                 # 'khaleeji' | 'ila_cc' | 'ila_account'
    txns: list[ParsedTxn]
    period_start: str | None = None     # ISO date of statement coverage
    period_end: str | None = None
    account_iban: str | None = None
    totals: dict = field(default_factory=dict)   # printed reconciliation totals for self-check
