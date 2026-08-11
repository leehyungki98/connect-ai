from datetime import date

from autotrader.execution.stop_monitor import ExitSignal, Holding, check_exits

D0 = date(2026, 7, 1)
TODAY = date(2026, 7, 20)


def holding(symbol="005930", qty=10, stop=9_000, target=12_000, horizon=30, entry=D0):
    return Holding(symbol, qty, entry, stop, target, horizon)


def test_stop_hit():
    assert check_exits([holding()], {"005930": 8_999}, TODAY) == [
        ExitSignal("005930", 10, "stop")
    ]


def test_stop_boundary_inclusive():
    assert check_exits([holding()], {"005930": 9_000}, TODAY)[0].kind == "stop"


def test_target_hit_inclusive():
    assert check_exits([holding()], {"005930": 12_000}, TODAY)[0].kind == "target"


def test_time_expiry():
    h = holding(horizon=19)  # 7/1 + 19일 <= 7/20
    assert check_exits([h], {"005930": 10_000}, TODAY) == [
        ExitSignal("005930", 10, "time")
    ]


def test_no_exit_within_range_and_horizon():
    assert check_exits([holding()], {"005930": 10_000}, TODAY) == []


def test_stop_priority_over_time():
    h = holding(horizon=1)  # 기간만료 상태 + 손절가 이탈
    assert check_exits([h], {"005930": 8_000}, TODAY)[0].kind == "stop"


def test_missing_price_no_signal():
    """시세 없으면 팔지 않는다."""
    assert check_exits([holding()], {}, TODAY) == []
    assert check_exits([holding()], {"005930": 0}, TODAY) == []


def test_output_sorted_by_symbol():
    hs = [holding("111111", stop=9_000), holding("000660", stop=9_000)]
    out = check_exits(hs, {"111111": 8_000, "000660": 8_000}, TODAY)
    assert [s.symbol for s in out] == ["000660", "111111"]
