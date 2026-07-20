"""밴드 판정 — 경계값 규칙: 정확히 ±5.0%p 는 유지, 초과(>)만 이탈."""
from longcore.rebalance import check_bands, deviations

TARGETS = {"ETF": 0.60, "GROWTH": 0.30, "CASH": 0.10}


def test_no_breach_at_targets():
    assert check_bands({"ETF": 0.60, "GROWTH": 0.30, "CASH": 0.10}, TARGETS, 0.05) == []


def test_exact_boundary_is_not_breach():
    # 정확히 +5.0%p — 명시된 규칙: 유지 (이탈 아님)
    w = {"ETF": 0.65, "GROWTH": 0.30, "CASH": 0.05}
    assert check_bands(w, TARGETS, 0.05) == []


def test_just_over_boundary_is_breach():
    w = {"ETF": 0.651, "GROWTH": 0.30, "CASH": 0.049}
    breached = check_bands(w, TARGETS, 0.05)
    assert "ETF" in breached and "CASH" in breached and "GROWTH" not in breached


def test_negative_deviation_breach():
    w = {"ETF": 0.54, "GROWTH": 0.36, "CASH": 0.10}
    breached = check_bands(w, TARGETS, 0.05)
    assert breached == ["ETF", "GROWTH"]


def test_deviations_signs():
    dev = deviations({"ETF": 0.58, "GROWTH": 0.33, "CASH": 0.09}, TARGETS)
    assert dev["ETF"] < 0 < dev["GROWTH"] and abs(dev["CASH"] + 0.01) < 1e-12
