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
