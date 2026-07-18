"""Khaleeji current-account statement parser (native text).

Each transaction spans 3 visual rows:
  1. DATE  <desc + # serial>  AMOUNT Dr./Cr.  BALANCE      (amount is always on the date row)
  2. Benefit Pay TRF ... to <beneficiary IBAN>
  3. BP<ref>
Sign: Dr. -> negative (outflow), Cr. -> positive (inflow: salary, profit).
NRT (.100) and 10% VAT (.010) lines are their own dated transactions sharing the parent
beneficiary IBAN, so they categorise with it automatically.

`parse_khaleeji_lines` is shared with the dormant OCR fallback.
"""
from __future__ import annotations

import re

from ..models import ParsedStatement, ParsedTxn
from ..normalise import extract_iban
from .base import StatementParser, statement_rows, parse_amount, full_text

_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})\b")
# amount/balance allow a leading dot (".100", ".000") as well as "8,259.010"
_MONEY = r"(?:\d[\d,]*)?\.\d{3}"
_AMT_DRCR = re.compile(rf"({_MONEY})\s+(Dr|Cr)\.\s+({_MONEY})")
_ALL_MONEY = re.compile(_MONEY)
_CONT = re.compile(r"Benefit Pay|^BP[0-9A-Z]+$|ref\.|/SNDT|IBAN|BH\d{2}[A-Z]{4}", re.I)
_COVERAGE = re.compile(r"Statement From\s+(\d{2}/\d{2}/\d{4})\s+To\s+(\d{2}/\d{2}/\d{4})")
_TXN_SIGNATURE = re.compile(r"(?:\d[\d,]*)?\.\d{3}\s+(?:Dr|Cr)\.")


def _iso(dm: re.Match) -> str:
    return f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"


def parse_khaleeji_lines(rows: list[str]) -> tuple[list[ParsedTxn], dict, str | None, str | None]:
    txns: list[ParsedTxn] = []
    cur: dict | None = None

    def finalize(c: dict) -> ParsedTxn:
        desc = " ".join(c["desc"]).strip()
        return ParsedTxn(
            txn_date=c["date"],
            amount=round(c["amount"], 3),
            raw_desc=desc,
            balance=c["balance"],
            counterparty_iban=extract_iban(desc),
        )

    for row in rows:
        dm = _DATE.match(row)
        am = _AMT_DRCR.search(row)
        if dm and am:
            if cur:
                txns.append(finalize(cur))
            amount = parse_amount(am.group(1))
            signed = amount if am.group(2) == "Cr" else -amount
            cur = {
                "date": _iso(dm),
                "amount": signed,
                "balance": parse_amount(am.group(3)),
                "desc": [row[dm.end():am.start()].strip()],
            }
        elif cur is not None and _CONT.search(row):
            cur["desc"].append(row.strip())
        # else: header/footer/noise -> ignore
    if cur:
        txns.append(finalize(cur))

    totals = _totals(rows)
    start = end = None
    for row in rows:
        cm = _COVERAGE.search(row)
        if cm:
            s = cm.group(1); e = cm.group(2)
            start = f"{s[6:10]}-{s[3:5]}-{s[0:2]}"
            end = f"{e[6:10]}-{e[3:5]}-{e[0:2]}"
            break
    if not start and txns:
        start = min(t.txn_date for t in txns)
        end = max(t.txn_date for t in txns)
    return txns, totals, start, end


def _totals(rows: list[str]) -> dict:
    """Footer: Balance B/Fwd | Total Debit | Total Credit | Book Balance."""
    for idx, row in enumerate(rows):
        if "Total Debit" in row and "Total Credit" in row:
            for cand in [row, *rows[idx + 1:idx + 3]]:
                nums = _ALL_MONEY.findall(cand)
                if len(nums) >= 3:
                    vals = [parse_amount(n) for n in nums]
                    return {"total_debit": vals[-3], "total_credit": vals[-2],
                            "book_balance": vals[-1]}
    return {}


class KhaleejiTextParser(StatementParser):
    source_account = "khaleeji"

    @classmethod
    def detect(cls, path, doc) -> bool:
        if not path.name.lower().startswith("accountfullstatement"):
            return False
        return _TXN_SIGNATURE.search(full_text(doc)) is not None  # native text present

    def parse(self, path, doc) -> ParsedStatement:
        txns, totals, start, end = parse_khaleeji_lines(statement_rows(doc))
        return ParsedStatement(source_account=self.source_account, txns=txns,
                               period_start=start, period_end=end, totals=totals)
