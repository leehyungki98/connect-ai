"""목표 2 검증(컴플라이언스): 절대규칙 5 강제 — 모의투자 외 전부 거부."""
from autotrader.gates.compliance import (
    PAPER_BASE_URL,
    OrderEndpoint,
    check_compliance,
)
from autotrader.gates.types import OrderIntent, Portfolio, Position

PF = Portfolio(10_000_000, 5_000_000,
               {"005930": Position(qty=10, value_krw=700_000)})

BUY_EP = OrderEndpoint(PAPER_BASE_URL, "VTTC0802U")
SELL_EP = OrderEndpoint(PAPER_BASE_URL, "VTTC0801U")


def test_valid_paper_buy_allowed():
    intent = OrderIntent("buy", "005930", 10, 70_000, 65_000)
    assert check_compliance(intent, BUY_EP, PF).allowed is True


def test_valid_paper_sell_within_holdings_allowed():
    intent = OrderIntent("sell", "005930", 10, 70_000)
    assert check_compliance(intent, SELL_EP, PF).allowed is True


# --- 절대규칙 5: 실전/미지 도메인·TR ID 전부 거부 ---

def test_real_trading_domain_rejected():
    ep = OrderEndpoint("https://openapi.koreainvestment.com:9443", "VTTC0802U")
    d = check_compliance(OrderIntent("buy", "005930", 1, 70_000, 65_000), ep, PF)
    assert d.allowed is False and "절대규칙 5" in d.reason


def test_unknown_domain_rejected():
    ep = OrderEndpoint("https://evil.example.com", "VTTC0802U")
    assert check_compliance(
        OrderIntent("buy", "005930", 1, 70_000, 65_000), ep, PF
    ).allowed is False


def test_real_tr_id_rejected():
    ep = OrderEndpoint(PAPER_BASE_URL, "TTTC0802U")  # 실전 매수 TR
    d = check_compliance(OrderIntent("buy", "005930", 1, 70_000, 65_000), ep, PF)
    assert d.allowed is False and "절대규칙 5" in d.reason


def test_tr_id_side_mismatch_rejected():
    d = check_compliance(  # 매수 주문에 매도 TR
        OrderIntent("buy", "005930", 1, 70_000, 65_000), SELL_EP, PF
    )
    assert d.allowed is False


# --- 기타 위반 ---

def test_bad_symbol_format_rejected():
    for sym in ("5930", "00593A", "0059300", ""):
        d = check_compliance(OrderIntent("buy", sym, 1, 70_000, 65_000), BUY_EP, PF)
        assert d.allowed is False, sym


def test_sell_over_holdings_rejected():
    d = check_compliance(OrderIntent("sell", "005930", 11, 70_000), SELL_EP, PF)
    assert d.allowed is False and "공매도" in d.reason


def test_sell_without_position_rejected():
    d = check_compliance(OrderIntent("sell", "000660", 1, 100_000), SELL_EP, PF)
    assert d.allowed is False and "공매도" in d.reason


def test_invalid_side_rejected():
    d = check_compliance(OrderIntent("hold", "005930", 1, 70_000), BUY_EP, PF)
    assert d.allowed is False
