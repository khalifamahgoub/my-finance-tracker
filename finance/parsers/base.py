"""Parser interface + shared text-geometry helpers.

Bank statements are columnar, but PyMuPDF's linear text reads columns out of order.
`rows_by_y` reconstructs the *visual* rows by clustering words on their y-coordinate
and sorting left-to-right — the reliable basis for every native-text parser here.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections import defaultdict

from ..models import ParsedStatement

_AMOUNT = re.compile(r"-?\d[\d,]*\.\d{2,3}")


class StatementParser(ABC):
    source_account: str = "unknown"

    def __init__(self, cfg=None):
        self.cfg = cfg

    @classmethod
    @abstractmethod
    def detect(cls, path, doc) -> bool:
        """True if this parser handles the given PDF (filename + text signature)."""

    @abstractmethod
    def parse(self, path, doc) -> ParsedStatement:
        ...


def rows_by_y(page, bucket: float = 2.0) -> list[tuple[float, str]]:
    """Reconstruct visual rows: cluster words into 'bucket'-pt y-bands, sort each L->R."""
    lines: dict[int, list] = defaultdict(list)
    for w in page.get_text("words"):        # (x0, y0, x1, y1, word, block, line, wordno)
        lines[round(w[1] / bucket)].append(w)
    out: list[tuple[float, str]] = []
    for k in sorted(lines):
        ws = sorted(lines[k], key=lambda w: w[0])
        y0 = min(w[1] for w in ws)
        out.append((y0, " ".join(w[4] for w in ws)))
    return out


def statement_rows(doc) -> list[str]:
    """All visual rows across all pages, top-to-bottom, page order preserved."""
    out: list[str] = []
    for page in doc:
        out.extend(text for _, text in rows_by_y(page))
    return out


def parse_amount(token: str) -> float:
    return float(token.replace(",", ""))


def amounts_in(text: str) -> list[float]:
    return [parse_amount(m.group(0)) for m in _AMOUNT.finditer(text)]


def full_text(doc) -> str:
    return "\n".join(page.get_text() for page in doc)
