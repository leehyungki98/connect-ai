"""재무 스냅샷 — 멱등 적재, 결측 정규화, 넓은 지표 포착."""
from datetime import date

from longcore import snapshot, thesis
from longcore.config import THESIS_CRITERIA

INFO = {
    "profitMargins": 0.63, "freeCashflow": 4.6e10, "revenueGrowth": 0.85,
    "forwardPE": 15.79, "trailingPegRatio": 0.56, "trailingPE": 42.0,
    "marketCap": 4.9e12, "beta": 2.21,
}


def test_capture_extracts_metrics():
    rec = snapshot.capture("NVDA", INFO, note="보유", today=date(2026, 7, 20))
    assert rec["ticker"] == "NVDA" and rec["date"] == "2026-07-20"
    assert rec["metrics"]["net_margin"] == 0.63
    assert rec["metrics"]["forward_pe"] == 15.79
    assert rec["schema"] == snapshot.SCHEMA_VERSION


def test_missing_fields_are_none_and_listed():
    rec = snapshot.capture("NVDA", {"profitMargins": 0.5},
                           today=date(2026, 7, 20))
    assert rec["metrics"]["forward_pe"] is None
    assert "forward_pe" in rec["missing"]
    assert "net_margin" not in rec["missing"]


def test_nan_normalized_to_none():
    """NaN 이 그대로 저장되면 나중 판정에서 조용히 FAIL 이 된다 (기존 결함 재발 방지)."""
    rec = snapshot.capture("NVDA", {"profitMargins": float("nan")},
                           today=date(2026, 7, 20))
    assert rec["metrics"]["net_margin"] is None
    assert "net_margin" in rec["missing"]


def test_bool_is_not_a_number():
    rec = snapshot.capture("X", {"beta": True}, today=date(2026, 7, 20))
    assert rec["metrics"]["beta"] is None


def test_store_and_load_roundtrip(tmp_path):
    rec = snapshot.capture("NVDA", INFO, today=date(2026, 7, 20))
    snapshot.store_record(rec, ledger_dir=tmp_path)
    loaded = snapshot.load("NVDA", ledger_dir=tmp_path)
    assert len(loaded) == 1 and loaded[0]["metrics"]["peg"] == 0.56


def test_same_day_is_idempotent(tmp_path):
    """같은 날 두 번 돌려도 레코드는 하나 — 마지막 값이 이긴다."""
    snapshot.store_record(snapshot.capture("NVDA", INFO, today=date(2026, 7, 20)),
                          ledger_dir=tmp_path)
    snapshot.store_record(
        snapshot.capture("NVDA", {**INFO, "forwardPE": 16.5}, today=date(2026, 7, 20)),
        ledger_dir=tmp_path)
    loaded = snapshot.load("NVDA", ledger_dir=tmp_path)
    assert len(loaded) == 1
    assert loaded[0]["metrics"]["forward_pe"] == 16.5


def test_different_days_accumulate_in_order(tmp_path):
    for d in [date(2026, 8, 20), date(2026, 7, 20), date(2026, 9, 20)]:
        snapshot.store_record(snapshot.capture("NVDA", INFO, today=d),
                              ledger_dir=tmp_path)
    loaded = snapshot.load("NVDA", ledger_dir=tmp_path)
    assert [r["date"] for r in loaded] == ["2026-07-20", "2026-08-20", "2026-09-20"]


def test_as_fundamentals_feeds_thesis_judge():
    """스냅샷이 논지 판정에 그대로 들어가야 미래 백테스트가 성립한다."""
    rec = snapshot.capture("NVDA", INFO, today=date(2026, 7, 20))
    f = snapshot.as_fundamentals(rec)
    result = thesis.judge_criteria(f, THESIS_CRITERIA)
    assert all(v == thesis.PASS for v in result.values())


def test_coverage_counts_quarters(tmp_path):
    for d in [date(2026, 7, 20), date(2026, 8, 20), date(2026, 10, 20)]:
        snapshot.store_record(snapshot.capture("NVDA", INFO, today=d),
                              ledger_dir=tmp_path)
    cov = snapshot.coverage("NVDA", ledger_dir=tmp_path)
    assert cov["count"] == 3
    assert cov["quarters"] == 2          # Q3 두 번, Q4 한 번
