"""Statement parser registry. `detect()` on each is tried in order; first match wins,
so downstream code never branches on statement type — a new parser (or the OCR
fallback) just slots into REGISTRY.
"""
from __future__ import annotations

from .base import StatementParser, rows_by_y, statement_rows  # noqa: F401
from .ila_cc import IlaCcParser
from .ila_account import IlaAccountParser
from .khaleeji_text import KhaleejiTextParser
from .khaleeji_ocr import KhaleejiOcrParser

REGISTRY: list[type[StatementParser]] = [
    IlaCcParser,
    IlaAccountParser,
    KhaleejiTextParser,
    KhaleejiOcrParser,   # dormant fallback (only fires on a true scan with OCR configured)
]


def detect_parser(path, doc, cfg) -> StatementParser | None:
    for cls in REGISTRY:
        try:
            if cls.detect(path, doc):
                return cls(cfg)
        except Exception:
            continue
    return None
