"""주문 멱등성 원장: 같은 거래일 재실행에서 중복 주문이 나가지 않는지."""
import json

import pytest

from autotrader.execution.order_ledger import LedgerCorrupted, OrderLedger
from autotrader.gates.types import Portfolio
from autotrader.pipeline import run_premarket
from tests.test_pipeline import (
    TODAY, enter_brain, judge_pass_all, setup, uptrend_universe,
)


def run(kis, guard, store, tmp_path, runner, ledger=True):
    return run_premarket(
        kis, guard, store, uptrend_universe(), TODAY,
        tmp_path / "day_start.json", tmp_path / "cooldowns.json",
        proposer_runner=runner, judge_runner=judge_pass_all,
        order_ledger_file=(tmp_path / "run_lock.json") if ledger else None,
    )


def test_rerun_does_not_duplicate_buy(tmp_path):
    pf = Portfolio(10_000_000, 10_000_000, {})
    _ks, guard, store, kis = setup(tmp_path, pf, {})
    brain = enter_brain("005930", 14_000, 13_000, 16_000)

    r1 = run(kis, guard, store, tmp_path, brain)
    assert len(r1.buys_placed) == 1
    r2 = run(kis, guard, store, tmp_path, brain)
    assert r2.buys_placed == []
    assert any("재주문 생략" in s for s in r2.skipped)
    assert len(kis.orders) == 1  # 원장이 두 번째 발주를 막았다


def test_rerun_without_ledger_duplicates(tmp_path):
    """원장을 끄면 중복이 난다 — 위 테스트가 실제로 원장 덕분임을 보인다."""
    pf = Portfolio(10_000_000, 10_000_000, {})
    _ks, guard, store, kis = setup(tmp_path, pf, {})
    brain = enter_brain("005930", 14_000, 13_000, 16_000)

    run(kis, guard, store, tmp_path, brain, ledger=False)
    run(kis, guard, store, tmp_path, brain, ledger=False)
    assert len(kis.orders) == 2


def test_failed_order_is_retryable(tmp_path):
    led = OrderLedger(tmp_path / "l.json", TODAY)
    led.mark_sending("005930", "buy", qty=1, price=100)
    assert led.attempted("005930", "buy") is not None   # 결과 불명 → 재시도 금지
    led.mark_result("005930", "buy", False, "rejected")
    assert led.attempted("005930", "buy") is None       # 거래소 미도달 → 재시도 허용


def test_stale_date_ledger_is_discarded(tmp_path):
    path = tmp_path / "l.json"
    path.write_text(json.dumps({
        "date": "2026-07-17",
        "orders": {"2026-07-17-005930-buy": {"status": "placed"}},
    }), encoding="utf-8")
    assert OrderLedger(path, TODAY).attempted("005930", "buy") is None


def test_corrupted_ledger_blocks_run(tmp_path):
    path = tmp_path / "l.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(LedgerCorrupted):
        OrderLedger(path, TODAY)

    pf = Portfolio(10_000_000, 10_000_000, {})
    _ks, guard, store, kis = setup(tmp_path, pf, {})
    r = run_premarket(
        kis, guard, store, uptrend_universe(), TODAY,
        tmp_path / "day_start.json", tmp_path / "cooldowns.json",
        proposer_runner=enter_brain("005930", 14_000, 13_000, 16_000),
        judge_runner=judge_pass_all, order_ledger_file=path,
    )
    assert "order ledger corrupted" in r.blocked and kis.orders == []
