"""가드 — 킬스위치 차단, 현금 음수 방지, 단일 종목 상한, 주문 위생."""
from longcore.safety import guard, killswitch
from longcore.types import Order
from tests.fixtures import CFG, PRICES, cash_only_holding, holding_at_targets


def _tmp_state(monkeypatch, tmp_path):
    monkeypatch.setattr(killswitch, "STATE_FILE", tmp_path / "killswitch.json")


def _buy(ticker, qty, sleeve="GROWTH"):
    return Order(ticker=ticker, side="BUY", qty=qty, sleeve=sleeve,
                 ref_price=PRICES[ticker])


def test_ok_orders_pass(monkeypatch, tmp_path):
    _tmp_state(monkeypatch, tmp_path)
    h = cash_only_holding(cash=21_000.0)
    orders = [_buy("VOO", 10.0, "ETF")]      # $5,000 — 현금 내
    assert guard.check(orders, h, PRICES, CFG, 25).ok


def test_killswitch_blocks_all(monkeypatch, tmp_path):
    _tmp_state(monkeypatch, tmp_path)
    killswitch.engage("점검")
    h = cash_only_holding(cash=21_000.0)
    res = guard.check([_buy("VOO", 1.0, "ETF")], h, PRICES, CFG, 25)
    assert not res.ok and any("킬스위치" in r for r in res.reasons)


def test_negative_cash_blocked(monkeypatch, tmp_path):
    _tmp_state(monkeypatch, tmp_path)
    h = cash_only_holding(cash=1_000.0)
    res = guard.check([_buy("VOO", 10.0, "ETF")], h, PRICES, CFG, 25)  # $5,012.5
    assert not res.ok and any("현금 음수" in r for r in res.reasons)


def test_oversell_blocked(monkeypatch, tmp_path):
    _tmp_state(monkeypatch, tmp_path)
    h = holding_at_targets()
    have = h["positions"]["NVDA"]["shares"]
    res = guard.check(
        [Order("NVDA", "SELL", have * 2, "GROWTH", PRICES["NVDA"])],
        h, PRICES, CFG, 25)
    assert not res.ok and any("초과 매도" in r for r in res.reasons)


def test_single_stock_cap_blocked(monkeypatch, tmp_path):
    _tmp_state(monkeypatch, tmp_path)
    h = cash_only_holding(cash=21_000.0)
    res = guard.check([_buy("NVDA", 20.0)], h, PRICES, CFG, 25)  # $4,000 ≈ 19%
    assert not res.ok and any("실효 상한" in r for r in res.reasons)


def test_lookthrough_catches_what_direct_weight_misses(monkeypatch, tmp_path):
    """설계 개정의 회귀 테스트 — 직접 비중은 상한 아래인데 ETF 중복까지 세면 초과.

    NVDA 직접 11% (< 15%) + VOO 60% 경유 4.5% = 실효 15.5% > 15% → 차단돼야 한다.
    이전의 직접분-only 로직은 이걸 통과시켰다.
    """
    _tmp_state(monkeypatch, tmp_path)
    h = cash_only_holding(cash=20_000.0)
    orders = [_buy("VOO", 24.0, "ETF"), _buy("NVDA", 11.0)]
    res = guard.check(orders, h, PRICES, CFG, 25)
    assert not res.ok and any("NVDA" in r and "실효 상한" in r for r in res.reasons)


def test_commission_drag_on_cap_is_tolerated(monkeypatch, tmp_path):
    """최초 배분: 수수료 드래그로 목표 10% 가 10.0X% 로 보여도 통과해야 한다."""
    from longcore.rebalance import make_orders
    _tmp_state(monkeypatch, tmp_path)
    h = cash_only_holding(cash=21_000.0)
    orders = make_orders(h, PRICES, CFG, ["CASH"])
    assert guard.check(orders, h, PRICES, CFG, 25).ok


def test_bad_order_hygiene(monkeypatch, tmp_path):
    _tmp_state(monkeypatch, tmp_path)
    h = cash_only_holding()
    res = guard.check([Order("VOO", "BUY", -1.0, "ETF", 500.0)], h, PRICES, CFG, 25)
    assert not res.ok
