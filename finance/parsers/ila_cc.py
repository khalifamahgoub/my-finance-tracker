"""ila credit card statement parser (native text).

Layout (reconstructed visual rows):
  - "Account transactions": DATE  Payment Received|Cashback  AMOUNT  CR  (card credits)
  - "Transactions on Card ending-XXXX"      -> primary cardholder
  - "Supplementary Card ending-YYYY"        -> supplementary cardholder
  - purchase row: DATE  MERCHANT LOCATION CODE  [CUR foreign]  AMOUNT_BHD [CR]
  - FX purchase = 3 dated charges (merchant BHD + FOREIGN EXCHANGE MARKUP + VAT),
    interleaved with non-dated annotation rows (rate, %, "MERCHANT CUR x.xx") -> skipped.
Sign: CR -> positive (payment/refund/cashback); otherwise negative (spend).
"""
from __future__ import annotations

import re

from ..models import ParsedStatement, ParsedTxn
from .base import StatementParser, statement_rows, parse_amount, full_text

_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})\b")
_CARD = re.compile(r"card ending-(\d{4})", re.I)
_FX = re.compile(r"\b(USD|SAR|AED|EUR|GBP|KWD|QAR|OMR)\s+([\d,]*\.\d{2,3})\b")
_TRAILING_AMT = re.compile(r"\s*-?\d[\d,]*\.\d{2,3}\s*(CR)?\s*$", re.I)
_AMOUNT = re.compile(r"-?\d[\d,]*\.\d{2,3}")
_FX_META = ("FOREIGN EXCHANGE MARKUP", "VAT DEBIT", "VAT CREDIT")


class IlaCcParser(StatementParser):
    source_account = "ila_cc"

    @classmethod
    def detect(cls, path, doc) -> bool:
        # The "Credit Card Statement" title is part of the logo graphic (not text), so
        # key off the section header, which is in the text layer and unique to the card.
        if not path.name.lower().startswith("statement"):
            return False
        # "Card ending-" (hyphen) is the CC section header; the ila account uses
        # "Card ending 6236" (space) for the debit card, so the hyphen disambiguates.
        return "Card ending-" in full_text(doc)

    def parse(self, path, doc) -> ParsedStatement:
        rows = statement_rows(doc)
        cardholders = (self.cfg.cardholders if self.cfg else {}) or {}
        txns: list[ParsedTxn] = []
        cardholder: str | None = None
        section: str | None = None
        last_fx_merchant: str | None = None

        for row in rows:
            low = row.lower()
            if "account transactions" in low:
                section, cardholder = "payments", None
                continue
            cm = _CARD.search(low)
            if cm:
                section, cardholder = "card", cardholders.get(cm.group(1), "unknown")
                continue
            dm = _DATE.match(row)
            if not dm:
                continue  # annotation row (rate / % / "MERCHANT CUR x.xx")

            date_iso = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"
            rest = row[dm.end():].strip()

            nums = _AMOUNT.findall(rest)
            if not nums:
                continue
            is_credit = bool(re.search(r"\bCR\b\s*$", rest))
            amount_bhd = parse_amount(nums[-1])
            signed = amount_bhd if is_credit else -amount_bhd

            fx = _FX.search(rest)
            fx_currency = fx.group(1) if fx else None
            fx_amount = parse_amount(fx.group(2)) if fx else None

            # description = row minus trailing amount(s)/CR and any foreign-currency tail
            desc = rest
            if fx:
                desc = rest[:fx.start()].strip()
            else:
                desc = _TRAILING_AMT.sub("", rest).strip()

            up = desc.upper()
            if any(k in up for k in _FX_META):
                # FX markup / VAT line: attach the parent merchant so it categorises with it
                if last_fx_merchant:
                    desc = f"{desc} ({last_fx_merchant})"
            elif fx:
                last_fx_merchant = desc  # remember merchant for its following markup/VAT

            txns.append(ParsedTxn(
                txn_date=date_iso,
                amount=round(signed, 3),
                raw_desc=desc,
                cardholder=cardholder if section == "card" else None,
                fx_currency=fx_currency,
                fx_amount=fx_amount,
                section=section,
            ))

        start = min((t.txn_date for t in txns), default=None)
        end = max((t.txn_date for t in txns), default=None)
        return ParsedStatement(
            source_account=self.source_account,
            txns=txns,
            period_start=start,
            period_end=end,
            totals=self._totals(rows),
        )

    @staticmethod
    def _totals(rows: list[str]) -> dict:
        """Header summary row: Opening  TotalDebits  TotalCredits  Current  [MinDue] [DueDate].

        Only the summary row carries >= 4 monetary amounts while not starting with a
        transaction date; a trailing payment-due date (dd/mm/yyyy, no decimal) shares the
        visual line but is not a monetary amount, so the first four amounts are the totals.
        """
        for row in rows:
            nums = _AMOUNT.findall(row)
            if len(nums) >= 4 and _DATE.match(row) is None:
                vals = [parse_amount(n) for n in nums]
                return {"opening": vals[0], "total_debits": vals[1],
                        "total_credits": vals[2], "current": vals[3]}
        return {}
