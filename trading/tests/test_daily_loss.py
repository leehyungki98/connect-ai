from autotrader.config import DAILY_LOSS_LIMIT_BP, INITIAL_CAPITAL_KRW
from autotrader.safety.daily_loss import check_daily_loss

CAP = INITIAL_CAPITAL_KRW  # 10,000,000
LIMIT = CAP * DAILY_LOSS_LIMIT_BP // 10_000  # 300,000원


def test_limit_is_3pct_of_10m():
    assert LIMIT == 300_000


def test_loss_below_limit_ok():
    r = check_daily_loss(CAP, CAP - (LIMIT - 1))  # 손실 299,999원
    assert r.breached is False
    assert r.loss_krw == LIMIT - 1


def test_loss_exactly_at_limit_breached():
    r = check_daily_loss(CAP, CAP - LIMIT)  # 손실 300,000원
    assert r.breached is True


def test_loss_above_limit_breached():
    r = check_daily_loss(CAP, CAP - (LIMIT + 1))
    assert r.breached is True


def test_profit_not_breached():
    r = check_daily_loss(CAP, CAP + 500_000)
    assert r.breached is False
    assert r.loss_krw == -500_000


def test_invalid_equity_start_fails_closed():
    assert check_daily_loss(0, 100).breached is True
    assert check_daily_loss(-1, 100).breached is True
