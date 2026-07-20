"""paper_only 게이트 — 실거래 흔적 주문 거부, 페이퍼 엔진 외 체결 경로 차단."""
from longcore.gates import paper_only
from longcore.types import Order


def _order(**kw):
    base = dict(ticker="VOO", side="BUY", qty=1.0, sleeve="ETF", ref_price=500.0)
    base.update(kw)
    return Order(**base)


def test_clean_orders_pass():
    assert paper_only.check([_order(), _order(ticker="NVDA", sleeve="GROWTH")]).ok


def test_non_order_object_rejected():
    fake = {"ticker": "VOO", "side": "BUY", "broker": "KIS", "account": "123"}
    res = paper_only.check([fake])
    assert not res.ok and any("체결 경로 차단" in r for r in res.reasons)


def test_broker_marker_rejected():
    res = paper_only.check([_order(ticker="BROKER-X")])
    assert not res.ok


def test_live_side_rejected():
    res = paper_only.check([_order(side="LIVE_BUY")])
    assert not res.ok


def test_unknown_sleeve_rejected():
    res = paper_only.check([_order(sleeve="KIS")])
    assert not res.ok


def test_subclass_rejected():
    class SneakyOrder(Order):
        pass
    res = paper_only.check(
        [SneakyOrder(ticker="VOO", side="BUY", qty=1.0, sleeve="ETF",
                     ref_price=500.0)])
    assert not res.ok
