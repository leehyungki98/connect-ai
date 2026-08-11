import json
from datetime import date

from autotrader.safety.killswitch import KillSwitch
from autotrader.status import build_gate_status, write_gate_status

TODAY = date(2026, 7, 20)


def seed(tmp_path, equity_start=10_000_000):
    (tmp_path / "day_start.json").write_text(
        json.dumps({"date": TODAY.isoformat(), "equity": equity_start})
    )


def test_normal_status(tmp_path):
    seed(tmp_path)
    s = build_gate_status(tmp_path, TODAY, equity_now_krw=9_850_000)
    assert s["killswitch_engaged"] is False and s["daily_blocked"] is False
    assert s["daily_limit_used_pct"] == 0.5  # 손실 15만 / 한도 30만


def test_profit_clamps_to_zero(tmp_path):
    seed(tmp_path)
    s = build_gate_status(tmp_path, TODAY, equity_now_krw=10_500_000)
    assert s["daily_limit_used_pct"] == 0.0


def test_killswitch_and_block_reflected(tmp_path):
    seed(tmp_path)
    KillSwitch(tmp_path / "killswitch.json").engage("수동 정지")
    (tmp_path / "daily_block.json").write_text(
        json.dumps({"date": TODAY.isoformat(), "loss_krw": 1, "limit_krw": 1})
    )
    s = build_gate_status(tmp_path, TODAY)
    assert s["killswitch_engaged"] is True and "수동 정지" in s["killswitch_reason"]
    assert s["daily_blocked"] is True


def test_no_equity_now_gives_null_pct(tmp_path):
    seed(tmp_path)
    assert build_gate_status(tmp_path, TODAY)["daily_limit_used_pct"] is None


def test_stale_block_not_counted(tmp_path):
    seed(tmp_path)
    (tmp_path / "daily_block.json").write_text(
        json.dumps({"date": "2026-07-15", "loss_krw": 1, "limit_krw": 1})
    )
    assert build_gate_status(tmp_path, TODAY)["daily_blocked"] is False


def test_rejection_count_from_todays_log(tmp_path):
    seed(tmp_path)
    log = tmp_path / "premarket_log.jsonl"
    log.write_text(
        json.dumps({"date": "2026-07-19", "skipped": ["a", "b", "c"]}) + "\n"
        + json.dumps({"date": TODAY.isoformat(), "skipped": ["x 판정 기각(K1)", "y risk:"]}) + "\n"
    )
    assert build_gate_status(tmp_path, TODAY)["gate_rejections_today"] == 2


def test_write_creates_json(tmp_path):
    seed(tmp_path)
    out = write_gate_status(tmp_path, TODAY, 9_700_000)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["daily_limit_used_pct"] == 1.0  # 손실 30만 == 한도
