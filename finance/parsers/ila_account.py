"""ila current/savings account statement parser (native text; Bank ABC / ila).

Layout: DATE(DD-Mon-YY)  REFERENCE  DESCRIPTION  SIGNED_AMOUNT  BALANCE, with the
counterparty IBAN on a following continuation line. Amounts are already signed
(deposits +, outgoings -), matching our model directly.

Most rows are internal (Fawri from own Khaleeji, Credit Card Payment, Hassala sweeps,
transfers to own USD account); the real spend is ATM withdrawals and card purchases.
Internal classification happens in Phase 2 (transfers.py).

TWO PRE-NOV-2025 QUIRKS, both handled here so every statement reconciles:

1. Older layout (e.g. Statement-20250331): PyMuPDF renders a row's date+reference span
   at a slightly different baseline from its description+amount+balance span, so the
   2pt y-bucketing in `rows_by_y` sometimes splits one logical transaction into two
   adjacent clusters. The description cluster always sits directly ABOVE the date
   cluster. Depending on where the amount lands we see either:
     - amount on the date row  -> "Credit Card Payment" / "01-Mar-25 CC... -157.000 31.615"
     - amount on the desc row  -> "TAP*JAHEZ ... -8.130 23.485" / "03-Mar-25 646470977450"
   `_parse_rows` reunites the two clusters (see _looks_like_desc / the len(moneys)<2
   recovery branch) instead of dropping the second shape.

2. Bundled multi-statement PDFs (e.g. Statement-20251031): several statements are
   concatenated in one file. A page whose "Page N of M" has N==1 begins a new section.
   `_sections` splits on that so each statement's txns and printed totals stay together
   and reconcile independently, instead of one section's 0.000/0.000 summary masking
   another section's real transactions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import ParsedStatement, ParsedTxn
from ..normalise import extract_iban
from .base import StatementParser, parse_amount, rows_by_y, full_text

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE = re.compile(r"^(\d{2})-([A-Za-z]{3})-(\d{2})\b")
_MONEY = re.compile(r"-?(?:\d[\d,]*)?\.\d{3}")
_OUTGOINGS = re.compile(r"Total Outgoings\s+(-?[\d,]*\.\d{3})")
_DEPOSITS = re.compile(r"Total Deposits\s+([\d,]*\.\d{3})")
_PAGENO = re.compile(r"\bPage\s+(\d+)\s+of\s+(\d+)\b")
_BROUGHT = re.compile(r"BROUGHT FORWARD\s+([\d,]*\.\d{3})")
_CLOSING = re.compile(r"(?i)CLOSING BALANCE\s+([\d,]*\.\d{3})")

# Rows that continue an earlier transaction (never a split description cluster).
_CONT_MARKERS = ("POS TRANSACTION", "POS Transaction", "Card ending",
                 "From Mobile", "To Mobile", "BROUGHT FORWARD", "CLOSING BALANCE",
                 "Total Outgoings", "Total Deposits", "Statement of Account")


@dataclass
class IlaSection:
    """One statement within a (possibly bundled) PDF, with its own reconciliation."""
    rows: list[str]
    txns: list[ParsedTxn] = field(default_factory=list)
    totals: dict = field(default_factory=dict)
    brought_forward: float | None = None
    closing_balance: float | None = None
    n_merged_desc: int = 0   # rows whose description was pulled from the cluster above
    n_recovered: int = 0     # rows whose amount+balance were recovered from the cluster above


def _looks_like_desc(row: str) -> bool:
    """True if `row` is a standalone description cluster (a split transaction line),
    not a date row, an IBAN/reference continuation, or a summary marker."""
    if not row or _DATE.match(row):
        return False
    if not re.search(r"[A-Za-z]", row):
        return False
    if any(m in row for m in _CONT_MARKERS):
        return False
    if extract_iban(row):
        return False
    return True


class IlaAccountParser(StatementParser):
    source_account = "ila_account"

    @classmethod
    def detect(cls, path, doc) -> bool:
        if not path.name.lower().startswith("statement"):
            return False
        txt = full_text(doc)
        return "Total Outgoings" in txt or "Total Deposits" in txt

    def parse(self, path, doc) -> ParsedStatement:
        sections = self.sections(doc)
        txns = [t for s in sections for t in s.txns]
        start = min((t.txn_date for t in txns), default=None)
        end = max((t.txn_date for t in txns), default=None)
        # Aggregate the printed self-check totals across sections that print them.
        totals: dict = {}
        for s in sections:
            for k, v in s.totals.items():
                totals[k] = round(totals.get(k, 0.0) + v, 3)
        return ParsedStatement(
            source_account=self.source_account, txns=txns,
            period_start=start, period_end=end, totals=totals)

    # -- sectioning ---------------------------------------------------------

    def sections(self, doc) -> list[IlaSection]:
        """Split a (possibly bundled) PDF into independent statements and parse each.
        A page whose 'Page N of M' has N==1 starts a new section."""
        page_rows = [[t for _, t in rows_by_y(page)] for page in doc]
        groups: list[list[str]] = []
        cur: list[str] = []
        for prows in page_rows:
            pageno = None
            for r in prows:
                m = _PAGENO.search(r)
                if m:
                    pageno = int(m.group(1))
                    break
            if pageno == 1 and cur:
                groups.append(cur)
                cur = []
            cur.extend(prows)
        if cur:
            groups.append(cur)
        return [self._build_section(g) for g in groups]

    def _build_section(self, rows: list[str]) -> IlaSection:
        sec = IlaSection(rows=rows)
        self._parse_rows(rows, sec)
        sec.totals = self._totals(rows)
        for row in rows:
            mb = _BROUGHT.search(row)
            mc = _CLOSING.search(row)
            if mb and sec.brought_forward is None:
                sec.brought_forward = parse_amount(mb.group(1))
            if mc:
                sec.closing_balance = parse_amount(mc.group(1))  # last one wins (summary)
        return sec

    # -- row parsing --------------------------------------------------------

    def _parse_rows(self, rows: list[str], sec: IlaSection) -> None:
        cur: ParsedTxn | None = None
        for i, row in enumerate(rows):
            dm = _DATE.match(row)
            if dm:
                prev = rows[i - 1] if i > 0 else ""
                rest = row[dm.end():].strip()
                moneys = list(_MONEY.finditer(rest))
                if len(moneys) >= 2:
                    amount = parse_amount(moneys[-2].group(0))
                    balance = parse_amount(moneys[-1].group(0))
                    head = rest[:moneys[-2].start()].strip().split(None, 1)  # [ref, desc?]
                    desc = head[1] if len(head) > 1 else ""
                    if not desc and _looks_like_desc(prev):
                        # Older layout: amount stayed on the date row, description is the
                        # cluster above ("Credit Card Payment" / "01-Mar-25 ... -157.000").
                        desc = _strip_money(prev)
                        sec.n_merged_desc += 1
                    if not desc:
                        desc = head[0] if head else ""
                elif _looks_like_desc(prev):
                    # Older layout: the whole desc+amount+balance stayed on the cluster
                    # above and only date+reference is on this row ("TAP*JAHEZ ... -8.130"
                    # / "03-Mar-25 646470977450"). Recover amount+balance from prev.
                    pmoneys = list(_MONEY.finditer(prev))
                    if len(pmoneys) < 2:
                        continue
                    amount = parse_amount(pmoneys[-2].group(0))
                    balance = parse_amount(pmoneys[-1].group(0))
                    desc = prev[:pmoneys[-2].start()].strip()
                    sec.n_recovered += 1
                else:
                    continue
                yr = 2000 + int(dm.group(3))
                cur = ParsedTxn(
                    txn_date=f"{yr:04d}-{_MONTHS[dm.group(2).lower()]:02d}-{dm.group(1)}",
                    amount=round(amount, 3),
                    raw_desc=desc,
                    balance=balance,
                    counterparty_iban=extract_iban(desc),
                )
                sec.txns.append(cur)
            elif cur is not None and cur.counterparty_iban is None:
                ib = extract_iban(row)
                if ib:
                    cur.counterparty_iban = ib

    @staticmethod
    def _totals(rows: list[str]) -> dict:
        out: dict = {}
        for row in rows:
            mo = _OUTGOINGS.search(row)
            md = _DEPOSITS.search(row)
            if mo:
                out["total_outgoings"] = parse_amount(mo.group(1))
            if md:
                out["total_deposits"] = parse_amount(md.group(1))
        return out


def _strip_money(row: str) -> str:
    """Description text of a cluster, dropping any trailing amount/balance tokens."""
    m = _MONEY.search(row)
    return (row[:m.start()] if m else row).strip()
