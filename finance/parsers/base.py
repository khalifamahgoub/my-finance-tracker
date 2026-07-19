"""Parser interface + shared text-geometry helpers.

Bank statements are columnar, but PyMuPDF's linear text reads columns out of order.
`rows_by_y` reconstructs the *visual* rows by clustering words on their y-coordinate
and sorting left-to-right — the reliable basis for every native-text parser here.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod

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


def rows_by_y(page, tol: float = 3.0) -> list[tuple[float, str]]:
    """Reconstruct visual rows: cluster words whose y-baselines are within `tol` points of
    the previous word (consecutive-gap clustering), then sort each row left-to-right.

    Gap clustering replaces fixed `round(y/bucket)` banding, which split a single visual
    line when its words straddled a bucket boundary. Some statements (e.g. 2026 ila-CC)
    render the amount column ~1pt above the row baseline; with 2pt banding the amount and
    its date landed in adjacent buckets and became two rows, so the parser found a date
    with no amount and dropped every transaction. Distinct rows sit tens of points apart,
    well above `tol`, so they still separate cleanly.
    """
    words = sorted(page.get_text("words"), key=lambda w: (w[1], w[0]))
    clusters: list[list] = []
    for w in words:                         # (x0, y0, x1, y1, word, block, line, wordno)
        if clusters and w[1] - clusters[-1][-1][1] <= tol:
            clusters[-1].append(w)
        else:
            clusters.append([w])
    out: list[tuple[float, str]] = []
    for ws in clusters:
        ws_sorted = sorted(ws, key=lambda w: w[0])
        y0 = min(w[1] for w in ws)
        out.append((y0, " ".join(w[4] for w in ws_sorted)))
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
