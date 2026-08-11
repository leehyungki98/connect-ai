"""실효 비중(look-through) — ETF 내부 중복 노출까지 합산."""
import pytest

from longcore.config import INDEX_LOOKTHROUGH
from longcore.portfolio import breaches, effective_weights
from tests.fixtures import PRICES, holding_at_targets


def test_direct_only_when_no_etf():
    positions = {"NVDA": {"shares": 10.0}}     # $2,000
    eff = effective_weights(positions, 8_000.0, PRICES, INDEX_LOOKTHROUGH)
    assert eff["NVDA"] == pytest.approx(0.20)


def test_etf_overlap_added():
    """VOO 60% + NVDA 직접 10% → 실효 10% + 60%×7.5% = 14.5%."""
    h = holding_at_targets(nav=20_000.0)
    eff = effective_weights(h["positions"], h["cash_usd"], PRICES,
                            INDEX_LOOKTHROUGH)
    assert eff["NVDA"] == pytest.approx(0.10 + 0.60 * 0.075)   # 0.145
    assert eff["META"] == pytest.approx(0.10 + 0.60 * 0.025)   # 0.115


def test_tsm_has_no_overlap():
    """TSM 은 대만 기업 — S&P500 미포함이라 중복 없음."""
    h = holding_at_targets(nav=20_000.0)
    eff = effective_weights(h["positions"], h["cash_usd"], PRICES,
                            INDEX_LOOKTHROUGH)
    assert eff["TSM"] == pytest.approx(0.10)


def test_effective_exceeds_old_direct_cap():
    """설계 개정의 근거 — 직접분(10%)만 보면 통과하지만 실효는 10%를 넘는다."""
    h = holding_at_targets(nav=20_000.0)
    eff = effective_weights(h["positions"], h["cash_usd"], PRICES,
                            INDEX_LOOKTHROUGH)
    assert eff["NVDA"] > 0.10


def test_breaches_reports_over_cap():
    h = holding_at_targets(nav=20_000.0)
    eff = effective_weights(h["positions"], h["cash_usd"], PRICES,
                            INDEX_LOOKTHROUGH)
    watched = {"NVDA", "TSM", "META"}
    assert breaches(eff, 0.15, watched) == []            # 15% 상한 아래
    assert [t for t, _ in breaches(eff, 0.12, watched)] == ["NVDA"]   # 0.145만
    over = breaches(eff, 0.11, watched)                  # 11% 로 조이면
    assert [t for t, _ in over] == ["NVDA", "META"]      # 큰 순서


def test_zero_nav_raises():
    with pytest.raises(ValueError):
        effective_weights({}, 0.0, PRICES, INDEX_LOOKTHROUGH)
