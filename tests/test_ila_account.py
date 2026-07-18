"""Per-section reconciliation of the ila Debit Account statements.

Covers the two pre-Nov-2025 quirks the parser handles:
  * older split layout (description on a separate y-cluster from the date/amount),
  * bundled multi-statement PDFs (split into independent sections).

Each section must reconcile to within BHD 1 of its printed Total Outgoings/Total
Deposits, or — when a section prints no totals block — of its BROUGHT FORWARD ->
CLOSING BALANCE continuity. The raw PDFs live under Docs/ (gitignored), so the test
skips when they are not present.
"""
from __future__ import annotations

from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")
from finance.parsers.ila_account import IlaAccountParser  # noqa: E402

_FOLDER = (Path(__file__).resolve().parents[1]
           / "Docs" / "Bank Statement" / "ila Debit Account")
_TOL = 1.0
_FILES = sorted(_FOLDER.glob("Statement-*.pdf")) if _FOLDER.exists() else []


@pytest.mark.skipif(not _FILES, reason="ila Debit statements not present (Docs/ is gitignored)")
@pytest.mark.parametrize("pdf", _FILES, ids=lambda p: p.name)
def test_every_section_reconciles(pdf: Path):
    doc = fitz.open(pdf)
    sections = IlaAccountParser().sections(doc)
    assert sections, f"no sections parsed from {pdf.name}"
    for i, s in enumerate(sections):
        out = sum(t.amount for t in s.txns if t.amount < 0)
        dep = sum(t.amount for t in s.txns if t.amount > 0)
        po, pd = s.totals.get("total_outgoings"), s.totals.get("total_deposits")
        if po is not None and pd is not None:
            assert abs(out - po) < _TOL, f"{pdf.name} sec{i} outgoings {out} vs {po}"
            assert abs(dep - pd) < _TOL, f"{pdf.name} sec{i} deposits {dep} vs {pd}"
        else:
            assert s.brought_forward is not None and s.closing_balance is not None, \
                f"{pdf.name} sec{i} has neither printed totals nor BF/CB to reconcile against"
            expected = s.closing_balance - s.brought_forward
            assert abs((out + dep) - expected) < _TOL, \
                f"{pdf.name} sec{i} net {out + dep} vs BF->CB {expected}"


@pytest.mark.skipif(not _FILES, reason="ila Debit statements not present (Docs/ is gitignored)")
def test_bundled_pdf_splits_into_sections():
    """Statement-20251031 bundles an empty Oct statement with the real one."""
    bundled = _FOLDER / "Statement-20251031.pdf"
    if bundled not in _FILES:
        pytest.skip("bundled sample not present")
    sections = IlaAccountParser().sections(fitz.open(bundled))
    assert len(sections) == 2, "expected the empty + real October sections"
    assert not sections[0].txns, "first section should be the empty statement"
    assert sections[1].txns, "second section should hold the real transactions"
