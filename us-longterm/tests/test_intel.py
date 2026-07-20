"""정세 기록 — 검증·집계. 이 모듈은 매매를 발동시키지 않는다 (DESKS.md 경계 3)."""
from datetime import date

import pytest

from longcore import intel


def test_quarter_of():
    assert intel.quarter_of(date(2026, 7, 20)) == "2026-Q3"
    assert intel.quarter_of(date(2026, 1, 2)) == "2026-Q1"
    assert intel.quarter_of(date(2026, 12, 31)) == "2026-Q4"


def test_record_and_load(tmp_path):
    intel.record("TSM", date(2026, 8, 14), "Reuters", "수출규제 확대",
                 -2, "지정학", "중국 매출 비중 20% 직접 타격", ledger_dir=tmp_path)
    intel.record("TSM", date(2026, 8, 20), "실적발표", "가이던스 상향",
                 1, "성장", "3분기 매출 가이던스 +8%", ledger_dir=tmp_path)
    entries = intel.load_quarter("TSM", "2026-Q3", ledger_dir=tmp_path)
    assert len(entries) == 2
    assert entries[0]["impact"] == -2


def test_load_missing_returns_empty(tmp_path):
    assert intel.load_quarter("NVDA", "2026-Q1", ledger_dir=tmp_path) == []


def test_impact_range_enforced(tmp_path):
    with pytest.raises(ValueError):
        intel.record("NVDA", date(2026, 8, 1), "x", "y", -5, "성장", "근거",
                     ledger_dir=tmp_path)


def test_axis_must_be_known(tmp_path):
    with pytest.raises(ValueError):
        intel.record("NVDA", date(2026, 8, 1), "x", "y", 1, "느낌", "근거",
                     ledger_dir=tmp_path)


def test_basis_required(tmp_path):
    """점수만 있고 근거 없는 기록은 거부 — 집계가 의미를 가지려면 근거가 있어야."""
    with pytest.raises(ValueError):
        intel.record("NVDA", date(2026, 8, 1), "x", "y", 1, "성장", "   ",
                     ledger_dir=tmp_path)


def test_summarize_aggregates_by_axis(tmp_path):
    for impact, axis in [(-2, "지정학"), (1, "성장"), (-1, "지정학")]:
        intel.record("TSM", date(2026, 8, 1), "src", "ev", impact, axis, "근거",
                     ledger_dir=tmp_path)
    s = intel.summarize(intel.load_quarter("TSM", "2026-Q3", ledger_dir=tmp_path))
    assert s["count"] == 3
    assert s["score"] == -2
    assert s["by_axis"]["지정학"] == -3
    assert s["worst"][0]["impact"] == -2
