"""[편집 금지 구역] 리밸런싱 게이트 — 셋 다 통과해야 체결 허용.

(a) 오늘이 분기 점검일인가 (1·4·7·10월 첫 거래일)
(b) 밴드 이탈이 실제 있는가
(c) 주문이 이탈 슬리브만 건드리는가

CASH 규칙: 현금은 다른 슬리브를 거치지 않고는 복원할 수 없는 잔여 슬리브다.
따라서 CASH 이탈은 전 슬리브 리밸런싱을 허가한다 (문서화된 결정적 규칙 —
INTEGRATION_DESIGN 참조).

최초 배분(check_init): 보유가 완전히 비어 있고 초기화 전일 때만, (a)·(b) 면제로
전 슬리브 매수를 1회 허가한다. 그 외 안전층(가드·paper_only)은 전부 적용된다.
"""
from datetime import date
from typing import Iterable, Sequence

from ..types import GateResult


def is_quarterly_check_day(today: date, trading_days: Sequence[date],
                           months: Iterable[int]) -> bool:
    """분기 점검일 = 해당 월(1·4·7·10)의 첫 거래일. trading_days 는 시세 인덱스."""
    if today.month not in set(months):
        return False
    month_days = [d for d in trading_days
                  if d.year == today.year and d.month == today.month]
    return bool(month_days) and today == min(month_days)


def licensed_tickers(breached, bucket_cfg: dict) -> set:
    """이탈 슬리브가 허가하는 종목 집합. CASH 이탈 → 전 슬리브."""
    sleeves = bucket_cfg["sleeves"]
    if "CASH" in breached:
        names = sleeves.keys()
    else:
        names = [s for s in breached if s in sleeves]
    out = set()
    for s in names:
        out |= set(sleeves[s])
    return out


def check(today: date, trading_days: Sequence[date], breached, orders,
          bucket_cfg: dict) -> GateResult:
    reasons = []
    if not is_quarterly_check_day(today, trading_days,
                                  bucket_cfg["rebalance_months"]):
        reasons.append(f"{today} 는 분기 점검일(1·4·7·10월 첫 거래일)이 아님")
    if not breached:
        reasons.append("밴드 이탈 없음 — 주문 불허")
    allowed = licensed_tickers(breached, bucket_cfg)
    for o in orders:
        if o.ticker not in allowed:
            reasons.append(f"{o.ticker}: 이탈 슬리브 밖 종목 — 거부")
    return GateResult(not reasons, tuple(reasons))


def check_init(holding: dict) -> GateResult:
    """최초 배분 허가 — 보유 0 · 초기화 전일 때만."""
    reasons = []
    if holding.get("initialized"):
        reasons.append("이미 초기화된 버킷 — 최초 배분 불가")
    if holding.get("positions"):
        reasons.append("보유 종목이 존재 — 최초 배분 불가")
    return GateResult(not reasons, tuple(reasons))
