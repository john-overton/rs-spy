from rs_spy.scan.config import ScanConfig


def test_iex_defaults_are_recalibrated_proxies():
    c = ScanConfig.for_feed("iex")
    # IEX volume is ~2-3% of consolidated SIP volume (see IMPLEMENTATION.md's
    # ADV-gate recalibration); these are proxies for the spec's 1M sh / $25M.
    assert c.feed == "iex"
    assert c.min_adv_shares < 100_000
    assert c.min_adv_dollars < 5_000_000
    assert c.min_price == 10.0
    assert c.adv_window == 20


def test_sip_preset_uses_the_spec_thresholds_verbatim():
    c = ScanConfig.for_feed("sip")
    assert c.feed == "sip"
    assert c.min_adv_shares == 1_000_000
    assert c.min_adv_dollars == 25_000_000


def test_unknown_feed_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        ScanConfig.for_feed("polygon")
