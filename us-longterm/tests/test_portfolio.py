"""NAV·비중·환노출 계산 + 버킷 네임스페이스 분리."""
import pytest

from longcore import store
from longcore.portfolio import fx_exposure, nav_usd, sleeve_values, weights
from tests.fixtures import CFG, PRICES, USDKRW, holding_at_targets


def test_nav_and_weights():
    h = holding_at_targets(nav=20_000.0)
    assert nav_usd(h, PRICES) == pytest.approx(20_000.0)
    w = weights(h, PRICES, CFG["sleeves"])
    assert w["ETF"] == pytest.approx(0.60)
    assert w["GROWTH"] == pytest.approx(0.30)
    assert w["CASH"] == pytest.approx(0.10)


def test_sleeve_values():
    h = holding_at_targets(nav=20_000.0)
    v = sleeve_values(h, PRICES, CFG["sleeves"])
    assert v["ETF"] == pytest.approx(12_000.0)
    assert v["GROWTH"] == pytest.approx(6_000.0)
    assert v["CASH"] == pytest.approx(2_000.0)


def test_fx_exposure_usd_and_krw():
    h = holding_at_targets(nav=20_000.0)
    fx = fx_exposure(h, PRICES, USDKRW)
    assert fx["usd_exposure_pct"] == pytest.approx(1.0)   # 전 자산 USD
    assert fx["nav_usd"] == pytest.approx(20_000.0)
    assert fx["nav_krw"] == pytest.approx(28_000_000.0)


def test_missing_price_fails_closed():
    h = holding_at_targets()
    with pytest.raises(KeyError):
        nav_usd(h, {"VOO": 500.0})   # 나머지 가격 누락 — 조용한 기본값 금지


def test_unknown_holding_ticker_rejected():
    h = holding_at_targets()
    h["positions"]["TSLA"] = {"shares": 1.0}
    with pytest.raises(ValueError):
        sleeve_values(h, {**PRICES, "TSLA": 100.0}, CFG["sleeves"])


def test_bucket_namespace_isolation(tmp_path):
    """가상의 두 번째 버킷을 넣어도 계산·저장이 분리되는지 (ISA 자리 검증 —
    버킷 분리만, ISA 로직 아님)."""
    isa_sleeves = {"ETF": {"VOO": 1.0}}
    h1 = holding_at_targets(nav=20_000.0)
    h2 = {"cash_usd": 500.0, "positions": {"VOO": {"shares": 1.0}},
          "initialized": True}
    holdings = {"해외증권": h1, "ISA_자리": h2}

    # 계산 분리
    assert nav_usd(holdings["해외증권"], PRICES) == pytest.approx(20_000.0)
    assert nav_usd(holdings["ISA_자리"], PRICES) == pytest.approx(1_000.0)
    w2 = weights(holdings["ISA_자리"], PRICES, isa_sleeves)
    assert w2["ETF"] == pytest.approx(0.5) and w2["CASH"] == pytest.approx(0.5)

    # 저장/로드 네임스페이스 보존
    p = tmp_path / "holdings.json"
    store.save_holdings(holdings, path=p)
    loaded = store.load_holdings(path=p)
    assert set(loaded) == {"해외증권", "ISA_자리"}
    assert loaded["해외증권"]["positions"]["VOO"]["shares"] == pytest.approx(
        h1["positions"]["VOO"]["shares"])
