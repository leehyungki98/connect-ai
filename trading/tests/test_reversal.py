"""약세장 모멘텀 반전 탐지 — 현빈의 새 눈. 순수 로직."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "rsr", Path(__file__).resolve().parents[1] / "scripts" / "run_shadow_report.py")
rsr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rsr)


def _r(rank, ret, breadth=0.20, status="closed"):
    return {"rank": rank, "ret_pct": ret, "breadth": breadth, "status": status}


def test_fires_when_top_worse_in_weak_market():
    rows = ([_r(1, -6), _r(2, -6), _r(3, -6), _r(2, -6), _r(1, -6)]      # 상위 5, -6%
            + [_r(6, 0), _r(7, -1), _r(8, 1), _r(9, 0), _r(6, 0)])       # 하위 5, ~0%
    d = rsr.detect_reversal(rows)
    assert d and d["n_top"] == 5 and d["n_low"] == 5
    assert d["low_avg"] - d["top_avg"] >= 3.0


def test_silent_without_enough_samples():
    rows = [_r(1, -6), _r(6, 0)]        # 각 1건 — 표본 부족
    assert rsr.detect_reversal(rows) is None


def test_silent_when_no_reversal():
    rows = [_r(1, 2)] * 5 + [_r(6, 1)] * 5   # 상위가 오히려 나음
    assert rsr.detect_reversal(rows) is None


def test_only_weak_breadth_counts():
    """폭 높은(≥30%) 구간은 반전 판정에서 뺀다 — 약세장 한정 가설이라."""
    rows = ([_r(1, -6, breadth=0.50)] * 5 + [_r(6, 0, breadth=0.50)] * 5)
    assert rsr.detect_reversal(rows) is None    # 다 폭 50% → 표본 0


def test_ignores_unclosed():
    rows = ([_r(1, -6)] * 5 + [_r(6, 0)] * 4
            + [_r(6, 99, status="unfilled")])   # 미체결은 안 셈
    assert rsr.detect_reversal(rows) is None     # 하위 청산 4건 <5
