import json
from datetime import date

from autotrader.gates.types import Portfolio
from autotrader.review import run_postmarket

TODAY = date(2026, 7, 20)


class FakeKIS:
    def __init__(self, pf):
        self.pf = pf

    def get_portfolio(self):
        return self.pf


def test_snapshot_logged_with_daily_return(tmp_path):
    (tmp_path / "day_start.json").write_text(
        json.dumps({"date": TODAY.isoformat(), "equity": 10_000_000})
    )
    kis = FakeKIS(Portfolio(10_150_000, 9_000_000, {}))
    s = run_postmarket(kis, tmp_path, TODAY)
    assert s["daily_return_bp"] == 150  # +1.5% — 관찰용 기록
    line = json.loads((tmp_path / "equity_log.jsonl").read_text().splitlines()[-1])
    assert line["equity_krw"] == 10_150_000


def test_no_day_start_still_logs(tmp_path):
    kis = FakeKIS(Portfolio(10_000_000, 10_000_000, {}))
    s = run_postmarket(kis, tmp_path, TODAY)
    assert s["daily_return_bp"] is None
    assert (tmp_path / "equity_log.jsonl").exists()


def test_llm_review_written(tmp_path):
    kis = FakeKIS(Portfolio(10_000_000, 10_000_000, {}))
    s = run_postmarket(
        kis, tmp_path, TODAY, llm_review=True,
        runner=lambda brain, prompt: "## 오늘 요약\n무거래",
    )
    assert s["review_file"] is not None
    assert "무거래" in (tmp_path / "reviews" / f"{TODAY.isoformat()}.md").read_text(
        encoding="utf-8"
    )


def test_llm_failure_does_not_break_snapshot(tmp_path):
    def boom(brain, prompt):
        raise RuntimeError("CLI down")
    kis = FakeKIS(Portfolio(10_000_000, 10_000_000, {}))
    s = run_postmarket(kis, tmp_path, TODAY, llm_review=True, runner=boom)
    assert s["review_error"] is not None
    assert (tmp_path / "equity_log.jsonl").exists()  # 스냅샷은 기록됨


def test_same_day_rerun_overwrites_not_appends(tmp_path):
    """마감을 하루에 두 번 돌려도 그 날짜 줄은 하나만 남는다.

    append 만 하면 집계·백테스트 비교에서 그 날 가중치가 배로 잡힌다.
    """
    kis = FakeKIS(Portfolio(10_000_000, 10_000_000, {}))
    run_postmarket(kis, tmp_path, TODAY)
    kis.pf = Portfolio(10_500_000, 9_000_000, {})
    run_postmarket(kis, tmp_path, TODAY)

    lines = (tmp_path / "equity_log.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1, f"같은 날짜가 {len(lines)}줄 — 중복 적재됨"
    assert json.loads(lines[0])["equity_krw"] == 10_500_000  # 마지막 값으로 갱신


def test_other_days_are_preserved(tmp_path):
    """덮어쓰기는 오늘 것만. 과거 기록은 그대로 남아야 한다."""
    log = tmp_path / "equity_log.jsonl"
    log.write_text(
        json.dumps({"date": "2026-07-17", "equity_krw": 9_000_000}) + "\n"
        + json.dumps({"date": "2026-07-18", "equity_krw": 9_500_000}) + "\n",
        encoding="utf-8",
    )
    kis = FakeKIS(Portfolio(10_000_000, 10_000_000, {}))
    run_postmarket(kis, tmp_path, TODAY)

    rows = [json.loads(x) for x in log.read_text().strip().splitlines()]
    assert [r["date"] for r in rows] == ["2026-07-17", "2026-07-18", TODAY.isoformat()]


def test_corrupt_line_is_preserved(tmp_path):
    """깨진 줄이 있어도 죽지 않고, 그 줄을 보존한다 (관찰 로그라 fail-open)."""
    log = tmp_path / "equity_log.jsonl"
    log.write_text("{ 깨진 줄\n", encoding="utf-8")
    kis = FakeKIS(Portfolio(10_000_000, 10_000_000, {}))
    run_postmarket(kis, tmp_path, TODAY)

    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "{ 깨진 줄"
    assert json.loads(lines[-1])["date"] == TODAY.isoformat()
