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
    res = guard.check([_buy("NVDA", 15.0)], h, PRICES, CFG, 25)  # $3,000 ≈ 14%
    assert not res.ok and any("단일 종목 상한" in r for r in res.reasons)


def test_bad_order_hygiene(monkeypatch, tmp_path):
    _tmp_state(monkeypatch, tmp_path)
    h = cash_only_holding()
    res = guard.check([Order("VOO", "BUY", -1.0, "ETF", 500.0)], h, PRICES, CFG, 25)
    assert not res.ok
