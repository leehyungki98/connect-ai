"""게이트 입력 타입: 주문 의도와 포트폴리오 스냅샷."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OrderIntent:
    side: str            # "buy" | "sell"
    symbol: str          # KRX 6자리 종목코드
    qty: int             # 주식 수
    limit_price: int     # 지정가 (원)
    stop_price: int | None = None  # 손절가. 매수 시 필수


@dataclass(frozen=True)
class Position:
    qty: int
    value_krw: int       # 현재 평가액 (원)


@dataclass(frozen=True)
class Portfolio:
    equity_krw: int      # 총 평가액 (현금 + 주식)
    cash_krw: int        # 가용 현금
    positions: dict[str, Position]
