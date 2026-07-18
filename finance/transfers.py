"""Internal-transfer netting + sinking-fund domain rules (pure, testable).

Netting is the load-bearing correctness rule (PRD 4.3): money moving between the user's
own accounts must never count as spend or income. The credit card's line-item purchases
are the real spend, counted exactly once; the funding legs (Khaleeji->ila Fawri, ila->CC
"Credit Card Payment", CC "Payment Received", Hassala sweeps) all net out.
"""
from __future__ import annotations


def is_own_internal(iban: str | None, own_ibans: set[str]) -> bool:
    """Counterparty is one of the user's own accounts."""
    return bool(iban) and iban.upper() in own_ibans


def is_desc_internal(source_account: str, norm_desc: str, internal_kws: list[str]) -> bool:
    """ila-account rows whose description marks them internal (CC payment, Hassala, own USD)."""
    if source_account != "ila_account":
        return False
    up = norm_desc.upper()
    return any(kw in up for kw in internal_kws)


def is_cc_payment(source_account: str, norm_desc: str) -> bool:
    """The 'Payment Received' credit on the card = the payment leg, not income."""
    return source_account == "ila_cc" and "PAYMENT RECEIVED" in norm_desc.upper()


def school_class(iban: str | None, amount: float, school_iban: str | None,
                 term_fee_min: float) -> tuple[str, int] | None:
    """(category, is_sinking) for a school-IBAN payment: >= term_fee_min is a term fee
    (draws on the sinking fund); smaller amounts are ECA/monthly education spend."""
    if not iban or not school_iban or iban.upper() != school_iban.upper():
        return None
    return ("Education", 1) if abs(amount) >= term_fee_min else ("Education", 0)


def remittance_class(iban: str | None, amount: float, remit_iban: str | None,
                     split: dict) -> str | None:
    """The Remittance Hub IBAN is shared between two recipients; split by amount
    band (only an IBAN rule can't, so the amount decides). Falls back to Recipient B."""
    if not iban or not remit_iban or iban.upper() != remit_iban.upper():
        return None
    a = abs(amount)
    band = float(split.get("band_amount", 180))
    if abs(a - band) <= band * 0.25:
        return "Recipient A"
    return "Recipient B"
