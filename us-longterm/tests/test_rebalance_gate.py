"""리밸런싱 게이트 — 분기 점검일·이탈 존재·이탈 슬리브 한정 + 최초 배분 게이트."""
from datetime import date

from longcore.gates import rebalance_gate
from longcore.types import Order
from tests.fixtures import CFG, cash_only_holding, holding_at_targets

JULY_DAYS = [date(2026, 7, d) for d in (1, 2, 3, 6, 7, 8, 9, 10)]
MONTHS = CFG["rebalance_months"]


def _order(ticker, sleeve):
    return Order(ticker=ticker, side="BUY", qty=1.0, sleeve=sleeve, ref_price=100.0)


def test_first_trading_day_is_check_day():
    assert rebalance_gate.is_quarterly_check_day(date(2026, 7, 1), JULY_DAYS, MONTHS)


def test_second_trading_day_is_not():
    assert not rebalance_gate.is_quarterly_check_day(date(2026, 7, 2), JULY_DAYS, MONTHS)


def test_non_quarter_month_is_not():
    days = [date(2026, 5, 1), date(2026, 5, 4)]
    assert not rebalance_gate.is_quarterly_check_day(date(2026, 5, 1), days, MONTHS)


def test_first_trading_day_after_holiday():
    days = [date(2026, 7, 3), date(2026, 7, 6)]   # 1·2일 휴장 가정
    assert rebalance_gate.is_quarterly_check_day(date(2026, 7, 3), days, MONTHS)


def test_gate_rejects_non_check_day():
    g = rebalance_gate.check(date(2026, 7, 2), JULY_DAYS, ["ETF"],
                             [_order("VOO", "ETF")], CFG)
    assert not g.ok


def test_gate_rejects_no_breach():
    g = rebalance_gate.check(date(2026, 7, 1), JULY_DAYS, [], [], CFG)
    assert not g.ok and any("이탈 없음" in r for r in g.reasons)


def test_gate_rejects_order_outside_breached_sleeve():
    g = rebalance_gate.check(date(2026, 7, 1), JULY_DAYS, ["GROWTH"],
                             [_order("VOO", "ETF")], CFG)
    assert not g.ok and any("이탈 슬리브 밖" in r for r in g.reasons)


def test_gate_passes_valid():
    g = rebalance_gate.check(date(2026, 7, 1), JULY_DAYS, ["GROWTH"],
                             [_order("NVDA", "GROWTH")], CFG)
    assert g.ok


def test_cash_breach_licenses_all_tickers():
    allowed = rebalance_gate.licensed_tickers(["CASH"], CFG)
    assert allowed == {"VOO", "NVDA", "TSM", "META"}


def test_init_gate_only_for_empty():
    assert rebalance_gate.check_init(cash_only_holding()).ok
    assert not rebalance_gate.check_init(holding_at_targets()).ok   # 초기화됨
    h = cash_only_holding()
    h["positions"] = {"VOO": {"shares": 1.0}}
    assert not rebalance_gate.check_init(h).ok                       # 보유 존재
