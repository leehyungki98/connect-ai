"""[편집 금지 구역] paper_only 게이트 — 페이퍼 엔진 외의 체결 경로 차단.

주문에 브로커/실거래 흔적이 있으면 무조건 거부 (스윙의 컴플라이언스 게이트에 대응).
이 데스크에는 브로커 SDK·API 키 자체가 없지만, 방어선은 코드로도 세운다.
"""
import dataclasses

from ..types import GateResult, Order

# 실거래 흔적 마커 — 주문의 문자열 필드에 나타나면 거부
_FORBIDDEN_MARKERS = (
    "broker", "account", "api", "live", "kis", "tr_id", "http", "order_id",
    "domain", "token", "appkey", "secret",
)

_ALLOWED_FIELDS = {f.name for f in dataclasses.fields(Order)}
_ALLOWED_SIDES = {"BUY", "SELL"}
_ALLOWED_SLEEVES = {"ETF", "GROWTH"}


def check(orders) -> GateResult:
    reasons = []
    for o in orders:
        if type(o) is not Order:
            reasons.append(f"페이퍼 Order 타입이 아님 — 체결 경로 차단: {type(o).__name__}")
            continue
        extra = set(vars(o)) - _ALLOWED_FIELDS
        if extra:
            reasons.append(f"{o.ticker}: 허용되지 않는 필드 {sorted(extra)}")
        if o.side not in _ALLOWED_SIDES:
            reasons.append(f"{o.ticker}: 허용되지 않는 side {o.side!r}")
        if o.sleeve not in _ALLOWED_SLEEVES:
            reasons.append(f"{o.ticker}: 허용되지 않는 sleeve {o.sleeve!r}")
        text = f"{o.ticker} {o.side} {o.sleeve}".lower()
        hits = [m for m in _FORBIDDEN_MARKERS if m in text]
        if hits:
            reasons.append(f"{o.ticker}: 실거래 흔적 마커 {hits} — 거부")
    return GateResult(not reasons, tuple(reasons))
