"""Dormant OCR fallback for genuinely scanned Khaleeji statements.

Today's corpus is 100% native text, so this never fires. It self-disables unless
`ocr.tesseract_cmd` is set in config/accounts.yaml AND the binary + pytesseract exist.
Kept pluggable so a future true scan needs zero downstream changes.
"""
from __future__ import annotations

import re

from ..models import ParsedStatement
from .base import StatementParser, full_text

_TXN_ROW = re.compile(r"\d+\.\d{3}\s+(Dr|Cr)\.")


class KhaleejiOcrParser(StatementParser):
    source_account = "khaleeji"

    @classmethod
    def detect(cls, path, doc) -> bool:
        name = path.name.lower()
        if not name.startswith("accountfullstatement"):
            return False
        # Only claim a file that is a REAL scan: native text has no Dr./Cr. rows.
        return _TXN_ROW.search(full_text(doc)) is None

    def parse(self, path, doc) -> ParsedStatement:
        if not (self.cfg and self.cfg.apply_ocr_settings()):
            raise RuntimeError(
                "Scanned Khaleeji statement detected but OCR is unavailable. "
                "Install tesseract and set ocr.tesseract_cmd in config/accounts.yaml.")
        import pytesseract  # lazy
        import fitz

        zoom = float((self.cfg.accounts.get("ocr", {}) or {}).get("zoom", 3.0))
        psm = int((self.cfg.accounts.get("ocr", {}) or {}).get("psm", 6))
        text_pages = []
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            img = pix.tobytes("png")
            from PIL import Image
            import io
            text_pages.append(pytesseract.image_to_string(
                Image.open(io.BytesIO(img)), config=f"--psm {psm}"))
        # Reuse the native parser's line logic on OCR'd text.
        from .khaleeji_text import parse_khaleeji_lines
        txns, totals, start, end = parse_khaleeji_lines("\n".join(text_pages).splitlines())
        return ParsedStatement(source_account=self.source_account, txns=txns,
                               period_start=start, period_end=end, totals=totals)
