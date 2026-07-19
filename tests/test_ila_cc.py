"""Reconciliation of the ila credit-card statements against their printed totals.

Each statement's parsed debits/credits must tie to its printed header summary
(Opening | Total Debits | Total Credits | Current) within BHD 1. This guards the
visual-row reconstruction in particular: the 2026 statements render the amount column
~1pt above the row baseline, which the old fixed y-bucketing split into separate rows
(amount divorced from its date), silently dropping every transaction. The raw PDFs live
under Docs/ (gitignored), so the test skips when they are not present.
"""
from __future__ import annotations

from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")
from finance.parsers.ila_cc import IlaCcParser  # noqa: E402

_FOLDER = (Path(__file__).resolve().parents[1]
           / "Docs" / "Bank Statement" / "ila CC")
_TOL = 1.0
_FILES = sorted(_FOLDER.glob("Statement-*.pdf")) if _FOLDER.exists() else []


@pytest.mark.skipif(not _FILES, reason="ila CC statements not present (Docs/ is gitignored)")
@pytest.mark.parametrize("pdf", _FILES, ids=lambda p: p.name)
def test_statement_reconciles(pdf: Path):
    st = IlaCcParser().parse(pdf, fitz.open(pdf))
    assert st.txns, f"no transactions parsed from {pdf.name} (row reconstruction regressed?)"
    debits = -sum(t.amount for t in st.txns if t.amount < 0)
    credits = sum(t.amount for t in st.txns if t.amount > 0)
    td, tc = st.totals.get("total_debits"), st.totals.get("total_credits")
    assert td is not None and tc is not None, f"{pdf.name}: no printed totals row found"
    assert abs(debits - td) < _TOL, f"{pdf.name} debits {debits:.3f} vs printed {td:.3f}"
    assert abs(credits - tc) < _TOL, f"{pdf.name} credits {credits:.3f} vs printed {tc:.3f}"
