"""목표 6 검증(체결 모델): 슬리피지·수수료·부분체결이 보수적으로 반영되는지."""
from autotrader.execution.fills import (
    fill_limit_buy,
    fill_limit_sell,
    fill_stop_sell,
    max_fill_qty,
)

VOL = 1_000_000  # 캡 = 10,000주


# --- 매수 ---

def test_buy_fill_at_open_plus_slippage():
    f = fill_limit_buy("005930", 10_000, 500, day_open=9_900, day_low=9_800, day_volume=VOL)
    # base 9900, 슬리피지 ceil(9900*0.0005)=5 → 9905
    assert f.qty == 500 and f.price == 9_905
    gross = 500 * 9_905
    assert f.commission_krw == -(-gross * 150 // 1_000_000)  # 올림
    assert f.net_krw == -(gross + f.commission_krw)


def test_buy_price_capped_at_limit():
    # 시가가 지정가 근처 → 슬리피지 더해도 지정가 초과 불가
    f = fill_limit_buy("005930", 10_000, 100, day_open=9_998, day_low=9_900, day_volume=VOL)
    assert f.price == 10_000


def test_buy_gap_up_above_limit_no_fill():
    assert fill_limit_buy("005930", 10_000, 100, 10_500, 10_100, VOL) is None


def test_buy_low_touches_limit_fills_at_limit():
    f = fill_limit_buy("005930", 10_000, 100, day_open=10_300, day_low=9_950, day_volume=VOL)
    assert f.price == 10_000  # base=limit, 슬리피지 상한도 limit


def test_buy_partial_fill_at_volume_cap():
    f = fill_limit_buy("005930", 10_000, 50_000, 9_900, 9_800, VOL)
    assert f.qty == 10_000  # 거래량 1% 캡


def test_buy_zero_volume_no_fill():
    assert fill_limit_buy("005930", 10_000, 100, 9_900, 9_800, 0) is None


# --- 손절 매도 ---

def test_stop_sell_normal():
    f = fill_stop_sell("005930", 9_000, 100, day_open=9_100, day_low=8_900, day_volume=VOL)
    # base=min(9100,9000)=9000, 슬리피지 ceil(4.5)=5 → 8995
    assert f.price == 8_995
    gross = 100 * 8_995
    assert f.commission_krw == -(-gross * 150 // 1_000_000)
    assert f.tax_krw == -(-gross * 1_800 // 1_000_000)
    assert f.net_krw == gross - f.commission_krw - f.tax_krw


def test_stop_sell_gap_down_fills_at_open():
    f = fill_stop_sell("005930", 9_000, 100, day_open=8_500, day_low=8_300, day_volume=VOL)
    assert f.price == 8_500 - 5  # ceil(8500*0.0005)=5


def test_stop_not_triggered():
    assert fill_stop_sell("005930", 9_000, 100, 9_500, 9_100, VOL) is None


# --- 목표가 매도 ---

def test_target_sell_at_target():
    f = fill_limit_sell("005930", 10_000, 100, day_open=9_800, day_high=10_200, day_volume=VOL)
    assert f.price == 10_000  # base=max(9800,10000)=10000, 슬리피지 깎여도 limit 미만 불가


def test_target_sell_gap_up_fills_above_target():
    f = fill_limit_sell("005930", 10_000, 100, day_open=10_500, day_high=10_600, day_volume=VOL)
    assert f.price == 10_500 - 6  # ceil(10500*0.0005)=6, target 이상 유지


def test_target_not_reached():
    assert fill_limit_sell("005930", 10_000, 100, 9_500, 9_900, VOL) is None


# --- 공통 ---

def test_max_fill_qty():
    assert max_fill_qty(1_000_000) == 10_000
    assert max_fill_qty(99) == 0
    assert max_fill_qty(-1) == 0


def test_round_trip_is_negative_at_same_price():
    """같은 가격에 사고팔면 비용 때문에 반드시 손실 (낙관 편향 방지 확인)."""
    buy = fill_limit_buy("005930", 10_000, 100, 10_000, 9_900, VOL)
    sell = fill_limit_sell("005930", 10_000, 100, 10_000, 10_000, VOL)
    assert buy.net_krw + sell.net_krw < 0
