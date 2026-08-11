"""보유 포지션 청산 판정: 손절 / 목표 / 기간만료. 절대규칙 2: 100% 결정적.

우선순위: stop > target > time (안전 우선).
시세가 없는 종목은 신호를 내지 않는다 — 시세 없이 팔지 않는다.
(호출측은 시세 누락을 별도 경고로 로깅할 것.)
horizon_days는 달력일 기준.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Holding:
    symbol: str
    qty: int
    entry_date: date
    stop_price: int
    target_price: int
    horizon_days: int


@dataclass(frozen=True)
class ExitSignal:
    symbol: str
    qty: int
    kind: str  # "stop" | "target" | "time"


def check_exits(
    holdings: list[Holding],
    current_prices: dict[str, int],
    today: date,
) -> list[ExitSignal]:
    signals = []
    for h in sorted(holdings, key=lambda x: x.symbol):  # 순서 재현성
        price = current_prices.get(h.symbol)
        if price is None or price <= 0 or h.qty <= 0:
            continue
        if price <= h.stop_price:
            signals.append(ExitSignal(h.symbol, h.qty, "stop"))
        elif price >= h.target_price:
            signals.append(ExitSignal(h.symbol, h.qty, "target"))
        elif (today - h.entry_date).days >= h.horizon_days:
            signals.append(ExitSignal(h.symbol, h.qty, "time"))
    return signals
