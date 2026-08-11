"""프리마켓 당일 재실행 가드.

스케줄러 재발동·수동 재실행이 겹치면 같은 날 주문이 두 배로 나간다.
2026-07-20 사후분석이 잡아낸 실제 사고(00:58, 01:04 두 번 실행)에서 나왔다.
"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_premarket import already_ran_today  # noqa: E402

TODAY = date(2026, 7, 20)


def _write(path: Path, *rows: dict) -> None:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def test_no_log_means_not_run(tmp_path):
    assert already_ran_today(tmp_path / "premarket_log.jsonl", TODAY) is False


def test_today_present_blocks(tmp_path):
    log = tmp_path / "premarket_log.jsonl"
    _write(log, {"date": TODAY.isoformat(), "brain_ok": True})
    assert already_ran_today(log, TODAY) is True


def test_only_other_days_does_not_block(tmp_path):
    log = tmp_path / "premarket_log.jsonl"
    _write(log, {"date": "2026-07-17"}, {"date": "2026-07-18"})
    assert already_ran_today(log, TODAY) is False


def test_failed_run_leaves_no_entry_so_retry_allowed(tmp_path):
    """중간에 실패한 실행은 로그를 남기지 않는다 → 재시도는 통과해야 한다.

    가드가 '실행 시도'가 아니라 '완주 기록'을 기준으로 삼는 이유.
    """
    log = tmp_path / "premarket_log.jsonl"
    _write(log, {"date": "2026-07-18", "brain_ok": True})
    assert already_ran_today(log, TODAY) is False


def test_corrupt_line_is_skipped_not_fatal(tmp_path):
    log = tmp_path / "premarket_log.jsonl"
    log.write_text(
        "{ 깨진 줄\n" + json.dumps({"date": TODAY.isoformat()}) + "\n",
        encoding="utf-8",
    )
    assert already_ran_today(log, TODAY) is True


def test_empty_lines_ignored(tmp_path):
    log = tmp_path / "premarket_log.jsonl"
    log.write_text("\n\n" + json.dumps({"date": "2026-07-18"}) + "\n\n", encoding="utf-8")
    assert already_ran_today(log, TODAY) is False
