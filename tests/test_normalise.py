from finance import normalise as n

KHALEEJI_LINE = ("OUTWARD Fawri+ To 1100000000000 # 62181722 "
                 "Benefit Pay TRF Fawri+ to BH00TEST00100001909136 BP1770035990PVOH")


def test_extract_iban():
    assert n.extract_iban(KHALEEJI_LINE) == "BH00TEST00100001909136"
    assert n.extract_iban("no iban here") is None
    assert n.extract_iban("BH00 TEST 0010 0001 9091 36") == "BH00TEST00100001909136"


def test_normalise_strips_volatile_refs():
    iban = n.extract_iban(KHALEEJI_LINE)
    norm = n.normalise_desc(KHALEEJI_LINE, iban)
    assert "BP1770035990PVOH" not in norm      # BenefitPay serial gone
    assert "62181722" not in norm               # # serial gone
    assert "1100000000000" not in norm          # switch account gone
    assert iban in norm                          # IBAN preserved for identity
    assert "FAWRI" in norm


def test_normalise_keeps_short_location_codes():
    norm = n.normalise_desc("TALABAT.COM SANABIS 048", None)
    assert norm == "TALABAT COM SANABIS 048"


def test_dedup_key_deterministic():
    a = n.dedup_key("ila_cc", "2026-01-20", -1.4, "FALCON HOSPITA MANAMA")
    b = n.dedup_key("ila_cc", "2026-01-20", -1.4, "FALCON HOSPITA MANAMA")
    assert a == b and len(a) == 40


def test_dedup_key_varies_with_amount_and_source():
    base = dict(txn_date="2026-01-20", norm_desc="X")
    assert n.dedup_key("ila_cc", amount=-1.0, **base) != n.dedup_key("ila_cc", amount=-2.0, **base)
    assert n.dedup_key("ila_cc", amount=-1.0, **base) != n.dedup_key("khaleeji", amount=-1.0, **base)


def test_transfers_to_different_ibans_do_not_collide():
    # Same day, same amount, same base text, different beneficiary -> distinct keys.
    d1 = n.normalise_desc("Benefit Pay TRF Fawri+ to X", "BH00OWNA00000000000001")
    d2 = n.normalise_desc("Benefit Pay TRF Fawri+ to X", "BH00OWNB00000000000002")
    assert n.dedup_key("khaleeji", "2026-02-09", -50.0, d1) != n.dedup_key("khaleeji", "2026-02-09", -50.0, d2)
