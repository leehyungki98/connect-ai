"""성과 지표 (관찰용). 절대수익은 목표가 아니라 결과로만 기록한다.

최적화 대상: 리스크조정 수익(샤프)과 MDD. 100% 결정적.
"""
from __future__ import annotations

import math
import statistics
from typing import Sequence


def daily_returns(equity_curve: Sequence[int]) -> list[float]:
    return [
        equity_curve[i] / equity_curve[i - 1] - 1
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] > 0
    ]


def max_drawdown_pct(equity_curve: Sequence[int]) -> float:
    """최대 낙폭 (0.25 = 25%)."""
    peak, mdd = 0, 0.0
    for e in equity_curve:
        peak = max(peak, e)
        if peak > 0:
            mdd = max(mdd, (peak - e) / peak)
    return mdd


def sharpe_annualized(equity_curve: Sequence[int], periods_per_year: int = 252) -> float:
    """연환산 샤프 (무위험수익률 0 가정). 데이터 부족/무변동이면 0.0."""
    rets = daily_returns(equity_curve)
    if len(rets) < 2:
        return 0.0
    std = statistics.pstdev(rets)
    if std == 0:
        return 0.0
    return statistics.mean(rets) / std * math.sqrt(periods_per_year)
