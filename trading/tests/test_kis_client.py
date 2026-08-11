"""목표 6 검증(KIS 클라이언트): 모의 도메인 강제 + 컴플라이언스 통과 없이는 전송 없음."""
import pytest

from autotrader.gates.types import OrderIntent, Portfolio, Position
from autotrader.kis.client import KISPaperClient

PF = Portfolio(10_000_000, 5_000_000, {"005930": Position(10, 700_000)})

TOKEN_RESP = (200, {"access_token": "tok123"})
ORDER_OK = (200, {"rt_cd": "0", "msg1": "정상", "output": {"ODNO": "0001234567"}})


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        return self.responses.pop(0)


def make_client(*responses):
    t = FakeTransport(responses)
    return KISPaperClient("key", "secret", "12345678-01", transport=t), t


def test_buy_order_hits_paper_domain_with_vt_tr():
    c, t = make_client(TOKEN_RESP, ORDER_OK)
    r = c.place_order(OrderIntent("buy", "005930", 10, 70_000, 66_000), PF)
    assert r.success is True and r.order_no == "0001234567"
    order_call = t.calls[-1]
    assert order_call["url"].startswith("https://openapivts.koreainvestment.com:29443")
    assert order_call["headers"]["tr_id"] == "VTTC0802U"
    assert order_call["body"] == {
        "CANO": "12345678", "ACNT_PRDT_CD": "01", "PDNO": "005930",
        "ORD_DVSN": "00", "ORD_QTY": "10", "ORD_UNPR": "70000",
    }


def test_all_requests_use_paper_domain_only():
    c, t = make_client(TOKEN_RESP, ORDER_OK)
    c.place_order(OrderIntent("sell", "005930", 10, 75_000), PF)
    assert all(
        call["url"].startswith("https://openapivts.koreainvestment.com:29443")
        for call in t.calls
    )


def test_compliance_rejection_sends_nothing():
    """보유 초과 매도(공매도) → HTTP 전송 0건."""
    c, t = make_client()  # 응답 없음 — 호출되면 IndexError로 실패
    r = c.place_order(OrderIntent("sell", "005930", 999, 75_000), PF)
    assert r.success is False and "compliance rejected" in r.message
    assert t.calls == []


def test_api_error_returns_failure():
    c, t = make_client(TOKEN_RESP, (200, {"rt_cd": "1", "msg1": "주문불가"}))
    r = c.place_order(OrderIntent("buy", "005930", 10, 70_000, 66_000), PF)
    assert r.success is False and "주문불가" in r.message


def test_token_cached_across_calls():
    c, t = make_client(TOKEN_RESP, ORDER_OK, ORDER_OK)
    c.place_order(OrderIntent("buy", "005930", 1, 70_000, 66_000), PF)
    c.place_order(OrderIntent("buy", "005930", 1, 70_000, 66_000), PF)
    token_calls = [x for x in t.calls if x["url"].endswith("/oauth2/tokenP")]
    assert len(token_calls) == 1


def test_get_price_parses_current_price():
    c, t = make_client(TOKEN_RESP, (200, {"rt_cd": "0", "output": {"stck_prpr": "71200"}}))
    assert c.get_price("005930") == 71_200
    assert "FID_INPUT_ISCD=005930" in t.calls[-1]["url"]


def test_get_portfolio_parses_balance():
    balance = (200, {
        "rt_cd": "0",
        "output1": [
            {"pdno": "005930", "hldg_qty": "10", "evlu_amt": "712000"},
            {"pdno": "000660", "hldg_qty": "0", "evlu_amt": "0"},  # 청산됨 → 제외
        ],
        "output2": [{"tot_evlu_amt": "10123456", "dnca_tot_amt": "9411456"}],
    })
    c, t = make_client(TOKEN_RESP, balance)
    pf = c.get_portfolio()
    assert pf.equity_krw == 10_123_456 and pf.cash_krw == 9_411_456
    assert pf.positions == {"005930": Position(10, 712_000)}


def test_bad_account_format_rejected():
    with pytest.raises(ValueError):
        KISPaperClient("k", "s", "1234-5678")
