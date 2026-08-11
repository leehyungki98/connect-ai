"""nav 기록의 ledger 미러링 — 재생성 불가 관찰 자산을 git 추적으로 백업.

state/nav_log.jsonl 은 append 원본이지만 git 미추적이라 폴더 삭제 시 영구 손실된다.
미러는 (date, bucket) 당 한 줄만 유지해 하루에 여러 번 실행해도 중복이 없어야 한다.
"""
import json

from longcore import store


def _rec(date, bucket="해외증권", nav=5000.0):
    return {"date": date, "bucket": bucket, "nav_usd": nav}


def _read(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def test_first_record_written(tmp_path):
    p = store.mirror_nav_record(_rec("2026-07-20"), ledger_dir=tmp_path)
    rows = _read(p)
    assert len(rows) == 1 and rows[0]["date"] == "2026-07-20"


def test_same_day_is_idempotent(tmp_path):
    """같은 날 두 번 실행 → 한 줄, 마지막 값이 이긴다."""
    store.mirror_nav_record(_rec("2026-07-20", nav=5000.0), ledger_dir=tmp_path)
    p = store.mirror_nav_record(_rec("2026-07-20", nav=5123.0), ledger_dir=tmp_path)
    rows = _read(p)
    assert len(rows) == 1
    assert rows[0]["nav_usd"] == 5123.0     # 갱신됨


def test_different_days_accumulate(tmp_path):
    for d in ["2026-07-22", "2026-07-20", "2026-07-21"]:
        store.mirror_nav_record(_rec(d), ledger_dir=tmp_path)
    p = store.OBSERVATIONS_DIR if False else tmp_path / "nav_log.jsonl"
    rows = _read(p)
    assert [r["date"] for r in rows] == ["2026-07-20", "2026-07-21", "2026-07-22"]


def test_buckets_kept_separate_same_day(tmp_path):
    """같은 날이라도 버킷이 다르면 별개 줄 — ISA 버킷 확장 대비."""
    store.mirror_nav_record(_rec("2026-07-20", bucket="해외증권"), ledger_dir=tmp_path)
    p = store.mirror_nav_record(_rec("2026-07-20", bucket="ISA"), ledger_dir=tmp_path)
    rows = _read(p)
    assert len(rows) == 2
    assert {r["bucket"] for r in rows} == {"해외증권", "ISA"}


def test_output_is_sorted_deterministic(tmp_path):
    """정렬 보장 — diff 가 깔끔해야 커밋 리뷰가 쉽다."""
    for d in ["2026-08-01", "2026-07-15", "2026-07-30"]:
        store.mirror_nav_record(_rec(d), ledger_dir=tmp_path)
    rows = _read(tmp_path / "nav_log.jsonl")
    dates = [r["date"] for r in rows]
    assert dates == sorted(dates)
