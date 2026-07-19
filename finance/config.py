"""Configuration + path resolution. Loads the hand-editable YAML in config/.

Everything the pipeline needs to know that isn't code lives in config/*.yaml, so a
future month never means touching Python. Paths are resolved relative to the repo
root (the parent of this package) so the tool runs from anywhere.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

_KW_CLEAN = re.compile(r"[^A-Z0-9 ]+")
_KW_WS = re.compile(r"\s+")


def normalise_keyword(s: str) -> str:
    """Match the norm_desc transform: uppercase, punctuation -> space, collapse."""
    return _KW_WS.sub(" ", _KW_CLEAN.sub(" ", s.upper())).strip()

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DB_PATH = ROOT / "finance.db"
INBOX = ROOT / "inbox"
ARCHIVE = ROOT / "archive"
OUTPUT = ROOT / "output"
DOCS = ROOT / "Docs"
SCHEMA_SQL = Path(__file__).resolve().parent / "schema.sql"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_config(name: str) -> dict[str, Any]:
    """Load config/<name>.yaml (your real, gitignored file) or fall back to the committed
    config/<name>.example.yaml sanitized template if the real one isn't present yet."""
    real = CONFIG_DIR / f"{name}.yaml"
    if real.exists():
        return _load_yaml(real)
    return _load_yaml(CONFIG_DIR / f"{name}.example.yaml")


@dataclass
class Config:
    """Loaded view of config/*.yaml plus resolved paths."""

    root: Path = ROOT
    db_path: Path = DB_PATH
    inbox: Path = INBOX
    archive: Path = ARCHIVE
    output: Path = OUTPUT
    docs: Path = DOCS
    accounts: dict[str, Any] = field(default_factory=dict)
    categories: dict[str, Any] = field(default_factory=dict)
    rules: dict[str, Any] = field(default_factory=dict)
    iban_map: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Config":
        return cls(
            accounts=load_config("accounts"),      # real (gitignored) or .example
            categories=load_config("categories"),  # real (gitignored) or .example
            rules=_load_yaml(CONFIG_DIR / "rules.yaml"),
            iban_map=load_config("iban_map"),      # real (gitignored) or .example
        )

    # ---- convenience accessors ----
    @cached_property
    def own_account_ibans(self) -> set[str]:
        return {i.strip().upper() for i in self.accounts.get("own_account_ibans", [])}

    @cached_property
    def cardholders(self) -> dict[str, str]:
        return {str(k): v for k, v in self.accounts.get("cardholders", {}).items()}

    @cached_property
    def school_iban(self) -> str | None:
        v = self.accounts.get("school_iban")
        return v.strip().upper() if v else None

    @cached_property
    def school_term_fee_min(self) -> float:
        return float(self.accounts.get("school_term_fee_min", 3800))

    @cached_property
    def keyword_rules(self) -> dict[str, list[str]]:
        """category -> keywords, normalised the SAME way as norm_desc (uppercase, punctuation
        -> space) so dotted keywords like 'CLAUDE.AI' match 'CLAUDE AI'. Order = precedence."""
        out: dict[str, list[str]] = {}
        learned = _load_yaml(CONFIG_DIR / "learned.yaml").get("keywords") or {}
        merged: dict[str, list] = {}
        for cat, kws in (self.rules.get("keywords") or {}).items():
            merged[cat] = list(kws or [])
        for cat, kws in learned.items():           # review-confirmed keywords, appended
            merged.setdefault(cat, []).extend(kws or [])
        for cat, kws in merged.items():
            seen: list[str] = []
            for k in kws:
                nk = normalise_keyword(str(k))
                if nk and nk not in seen:
                    seen.append(nk)
            out[cat] = seen
        return out

    @cached_property
    def reporting(self) -> dict[str, Any]:
        return self.accounts.get("reporting", {}) or {}

    @cached_property
    def sinking_funds(self) -> dict[str, Any]:
        return self.accounts.get("sinking_funds", {}) or {}

    @cached_property
    def tesseract_cmd(self) -> str | None:
        return (self.accounts.get("ocr", {}) or {}).get("tesseract_cmd")

    def apply_ocr_settings(self) -> bool:
        """Point pytesseract at the configured binary. Returns True if OCR is usable."""
        cmd = self.tesseract_cmd
        if not cmd or not Path(cmd).exists():
            return False
        try:
            import pytesseract  # lazy: absent unless the user installs it
        except ImportError:
            return False
        pytesseract.pytesseract.tesseract_cmd = cmd
        return True


# Money helpers — BHD is 3 dp (fils).
def to_fils(amount: float) -> float:
    return round(float(amount), 3)


MONEY_TOL = 0.001
