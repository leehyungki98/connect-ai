"""일일 손실 한도 검사. 절대규칙 2: 100% 결정적, 원 단위 정수 연산만.

손실 = 당일 시작 평가액 - 현재 평가액.
손실 >= 시작 평가액 * 한도bp / 10000 이면 위반.
"""
from __future__ import annotations

from dataclasses import dataclass

from autotrader.config import DAILY_LOSS_LIMIT_BP


@dataclass(frozen=True)
class LossCheck:
    breached: bool
    loss_krw: int   # 양수 = 손실
    limit_krw: int


def check_daily_loss(
    equity_start_krw: int,
    equity_now_krw: int,
    limit_bp: int = DAILY_LOSS_LIMIT_BP,
) -> LossCheck:
    if equity_start_krw <= 0:
        # 비정상 입력 → fail-closed
        return LossCheck(True, 0, 0)
    loss = equity_start_krw - equity_now_krw
    limit = equity_start_krw * limit_bp // 10_000
    return LossCheck(loss >= limit, loss, limit)
