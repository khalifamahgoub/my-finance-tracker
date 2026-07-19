from finance import transfers as tf
from finance.variance import _rag, GREEN, AMBER, RED

OWN = {"BH00OWNA00000000000001", "BH00OWNB00000000000002"}


def test_own_internal():
    assert tf.is_own_internal("BH00OWNA00000000000001", OWN)
    assert tf.is_own_internal("bh00ownb00000000000002", OWN)   # case-insensitive
    assert not tf.is_own_internal("BH00TEST00100001909136", OWN)
    assert not tf.is_own_internal(None, OWN)


def test_desc_internal_only_ila_account():
    kws = ["CREDIT CARD PAYMENT", "HASSALA", "TO MY ILA"]
    assert tf.is_desc_internal("ila_account", "Transfer to Hassala", kws)
    assert tf.is_desc_internal("ila_account", "credit card payment", kws)
    assert not tf.is_desc_internal("khaleeji", "Transfer to Hassala", kws)


def test_cc_payment():
    assert tf.is_cc_payment("ila_cc", "PAYMENT RECEIVED")
    assert not tf.is_cc_payment("ila_cc", "TALABAT COM")
    assert not tf.is_cc_payment("khaleeji", "PAYMENT RECEIVED")


def test_school_class():
    assert tf.school_class("BH00SCHL00000000000003", -3990, "BH00SCHL00000000000003", 3800) == ("Education", 1)
    assert tf.school_class("BH00SCHL00000000000003", -450, "BH00SCHL00000000000003", 3800) == ("Education", 0)
    assert tf.school_class("BH00TEST00100001909136", -3990, "BH00SCHL00000000000003", 3800) is None


def test_remittance_split():
    remit = "BH00RMIT00000000000004"
    split = {"band_amount": 180, "band_category": "Recipient A", "default_category": "Recipient B"}
    assert tf.remittance_class(remit, -180, remit, split) == "Recipient A"   # near the band
    assert tf.remittance_class(remit, -350, remit, split) == "Recipient B"   # default
    assert tf.remittance_class("BH00OWNA00000000000001", -180, remit, split) is None


def test_rag_expense():
    assert _rag(100, 90, False, 15) == GREEN     # under plan
    assert _rag(100, 110, False, 15) == AMBER    # up to 15% over
    assert _rag(100, 130, False, 15) == RED      # >15% over


def test_rag_income():
    assert _rag(100, 110, True, 15) == GREEN     # at/over target is good
    assert _rag(100, 90, True, 15) == AMBER
    assert _rag(100, 50, True, 15) == RED
