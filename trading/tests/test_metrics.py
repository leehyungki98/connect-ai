import math
import statistics

import pytest

from autotrader.metrics import daily_returns, max_drawdown_pct, sharpe_annualized


def test_max_drawdown():
    assert max_drawdown_pct([100, 120, 90, 130]) == (120 - 90) / 120
    assert max_drawdown_pct([100, 110, 120]) == 0.0
    assert max_drawdown_pct([]) == 0.0


def test_daily_returns():
    assert daily_returns([100, 110, 99]) == pytest.approx([0.1, 99 / 110 - 1])


def test_sharpe_formula():
    curve = [100, 101, 100, 101, 100]
    rets = daily_returns(curve)
    expected = statistics.mean(rets) / statistics.pstdev(rets) * math.sqrt(252)
    assert sharpe_annualized(curve) == expected


def test_sharpe_degenerate_cases():
    assert sharpe_annualized([100]) == 0.0
    assert sharpe_annualized([100, 100, 100]) == 0.0  # 무변동
