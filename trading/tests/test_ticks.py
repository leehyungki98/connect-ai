from autotrader.execution.ticks import round_down_to_tick


def test_tick_bands():
    assert round_down_to_tick(1_999) == 1_999      # <2000: 1원
    assert round_down_to_tick(4_998) == 4_995      # <5000: 5원
    assert round_down_to_tick(19_995) == 19_990    # <20000: 10원
    assert round_down_to_tick(49_999) == 49_950    # <50000: 50원
    assert round_down_to_tick(199_950) == 199_900  # <200000: 100원
    assert round_down_to_tick(499_999) == 499_500  # <500000: 500원
    assert round_down_to_tick(1_234_567) == 1_234_000


def test_already_on_tick_unchanged():
    assert round_down_to_tick(70_000) == 70_000
    assert round_down_to_tick(2_000) == 2_000


def test_nonpositive():
    assert round_down_to_tick(0) == 0
    assert round_down_to_tick(-100) == 0
