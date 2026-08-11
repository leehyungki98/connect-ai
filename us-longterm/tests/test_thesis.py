"""논지 재판정 — 3기준 재적용, 2분기 연속 미달 시 퇴출 후보, VOO 흡수."""
import pytest

from longcore import thesis
from longcore.config import (THESIS_CONSECUTIVE_OUT, THESIS_CRITERIA,
                             THESIS_FAIL_THRESHOLD)
from tests.fixtures import CFG

C = THESIS_CRITERIA

HEALTHY = {"net_margin": 0.63, "free_cashflow": 1e10,
           "revenue_growth": 0.85, "forward_pe": 15.8, "peg": 0.56}
BROKEN = {"net_margin": 0.039, "free_cashflow": -1e9,
          "revenue_growth": 0.16, "forward_pe": 149.0, "peg": 4.77}


def test_healthy_passes_all():
    r = thesis.judge_criteria(HEALTHY, C)
    assert all(v == thesis.PASS for v in r.values())
    assert not thesis.quarter_verdict(r, THESIS_FAIL_THRESHOLD)


def test_broken_fails_and_is_flagged():
    r = thesis.judge_criteria(BROKEN, C)
    assert r["수익성"] == thesis.FAIL and r["밸류"] == thesis.FAIL
    assert thesis.quarter_verdict(r, THESIS_FAIL_THRESHOLD)


def test_valuation_passes_if_either_metric_ok():
    """선행PER 30↓ '또는' PEG 1.5↓ — 하나만 통과해도 합격."""
    r = thesis.judge_criteria(
        {**HEALTHY, "forward_pe": 63.0, "peg": 1.2}, C)
    assert r["밸류"] == thesis.PASS
    r2 = thesis.judge_criteria(
        {**HEALTHY, "forward_pe": 63.0, "peg": 1.9}, C)
    assert r2["밸류"] == thesis.FAIL          # PLTR 케이스


def test_missing_data_is_unknown_not_fail():
    """결측을 퇴출 근거로 쓰면 데이터 사고가 매도가 된다 — UNKNOWN 으로 둔다."""
    r = thesis.judge_criteria({"revenue_growth": 0.20}, C)
    assert r["수익성"] == thesis.UNKNOWN and r["밸류"] == thesis.UNKNOWN
    assert not thesis.quarter_verdict(r, THESIS_FAIL_THRESHOLD)


def test_nan_is_unknown_not_fail():
    """회귀 — NaN 이 조용히 FAIL 이 되던 결함 (2026-07-20 역사적 검증에서 발견).

    `nan >= 0.15` 는 False 라서 결측이 기준 미달로 카운트됐다. 결측 2개면 '미달',
    2분기 연속이면 퇴출 제안까지 갔다 — 데이터 사고가 매도가 되는 경로다.
    """
    nan = float("nan")
    r = thesis.judge_criteria(
        {"net_margin": nan, "free_cashflow": nan, "revenue_growth": nan,
         "forward_pe": nan, "peg": nan}, C)
    assert all(v == thesis.UNKNOWN for v in r.values())
    assert not thesis.quarter_verdict(r, THESIS_FAIL_THRESHOLD)


def test_first_period_missing_growth_is_unknown():
    """직전값이 없는 첫 회계연도 — 성장률 계산 불가는 미달이 아니다."""
    r = thesis.judge_criteria({**HEALTHY, "revenue_growth": float("nan")}, C)
    assert r["성장"] == thesis.UNKNOWN
    assert r["수익성"] == thesis.PASS


def test_partial_nan_still_judges_available_criteria():
    """일부만 결측이면 나머지는 정상 판정해야 한다 — 전부 UNKNOWN 으로 뭉개지 않는다."""
    r = thesis.judge_criteria(
        {**BROKEN, "forward_pe": float("nan"), "peg": None}, C)
    assert r["밸류"] == thesis.UNKNOWN
    assert r["수익성"] == thesis.FAIL      # 이익률 3.9% + FCF 적자
    # 매출 +16% 는 기준(+15%) 을 통과한다. 원본 논지 문서가 TSLA 를 "0/3" 으로
    # 적었지만 숫자대로는 1/3 이다 — 결론(편입 불가)은 같으나 기록이 부정확했다.
    assert r["성장"] == thesis.PASS


def test_single_bad_quarter_does_not_exit():
    history = {"NVDA": [False, True]}          # 최근 1분기만 미달
    assert thesis.exit_candidates(history, THESIS_CONSECUTIVE_OUT) == []


def test_two_consecutive_bad_quarters_exits():
    history = {"NVDA": [False, True, True]}
    assert thesis.exit_candidates(history, THESIS_CONSECUTIVE_OUT) == ["NVDA"]


def test_recovery_resets_the_streak():
    history = {"META": [True, True, False]}    # 회복하면 연속 끊김
    assert thesis.exit_candidates(history, THESIS_CONSECUTIVE_OUT) == []


def test_apply_exits_redistributes_survivors():
    sleeves = thesis.apply_exits(CFG["sleeves"], ["NVDA"], "VOO")
    growth = sleeves["GROWTH"]
    assert "NVDA" not in growth
    assert set(growth) == {"TSM", "META"}
    assert sum(growth.values()) == 1.0        # 재정규화
    assert growth["TSM"] == 0.5


def test_all_growth_exits_absorbed_into_etf():
    sleeves = thesis.apply_exits(CFG["sleeves"], ["NVDA", "TSM", "META"], "VOO")
    assert sleeves["GROWTH"] == {}
    targets = thesis.absorb_targets(CFG["targets"], sleeves)
    assert targets["GROWTH"] == 0.0
    assert targets["ETF"] == pytest.approx(0.90)   # 60 + 30
    assert targets["CASH"] == 0.10            # 현금은 그대로 — 시장을 떠나지 않는다


def test_absorb_noop_when_growth_alive():
    targets = thesis.absorb_targets(CFG["targets"], CFG["sleeves"])
    assert targets == CFG["targets"]
