"""공용 타입 — 결정적 코드 전용."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Order:
    """페이퍼 주문. 체결가는 페이퍼 엔진이 실행일 수정종가로 정한다.

    ref_price 는 주문 생성 시점 가격 — 가드의 현금 시뮬레이션 참조용.
    """
    ticker: str
    side: str          # "BUY" | "SELL"
    qty: float         # 주식 수 (페이퍼라 소수 주 허용)
    sleeve: str        # "ETF" | "GROWTH"
    ref_price: float


@dataclass(frozen=True)
class GateResult:
    ok: bool
    reasons: tuple = field(default_factory=tuple)   # 거부 사유 (ok=False 일 때)
