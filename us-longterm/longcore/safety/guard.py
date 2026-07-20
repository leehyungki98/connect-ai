"""[편집 금지 구역] 가드 — 체결 직전 최종 점검 (킬스위치·현금 음수·단일 종목 상한).

순수 점검 함수 + 킬스위치 조회. 하나라도 걸리면 전체 거부 (부분 체결 없음).
"""
from ..types import GateResult, Order
from . import killswitch

_TOL = 1e-6


def check(orders, holding: dict, prices: dict, bucket_cfg: dict,
          commission_bps: int) -> GateResult:
    reasons = []

    ks = killswitch.status()
    if ks["engaged"]:
        reasons.append(f"킬스위치 차단: {ks['reason']}")

    # 주문 기본 위생
    for o in orders:
        if not isinstance(o, Order):
            reasons.append(f"Order 타입 아님: {o!r}")
            continue
        if o.side not in ("BUY", "SELL"):
            reasons.append(f"허용되지 않는 side: {o.side}")
        if not (o.qty > 0):
            reasons.append(f"{o.ticker}: qty 는 양수여야 함 ({o.qty})")
        if not (o.ref_price > 0):
            reasons.append(f"{o.ticker}: ref_price 는 양수여야 함 ({o.ref_price})")
        if o.ticker not in prices:
            reasons.append(f"{o.ticker}: 가격 없음")
    if reasons:
        return GateResult(False, tuple(reasons))

    # 현금 시뮬레이션 — 페이퍼 엔진과 동일 순서 (SELL 먼저, BUY 나중)
    c = commission_bps / 10_000.0
    cash = holding["cash_usd"]
    positions = {t: p["shares"] for t, p in holding.get("positions", {}).items()}
    for o in orders:
        if o.side == "SELL":
            have = positions.get(o.ticker, 0.0)
            if o.qty > have + _TOL:
                reasons.append(f"{o.ticker}: 보유({have}) 초과 매도({o.qty})")
                continue
            positions[o.ticker] = have - o.qty
            cash += o.qty * prices[o.ticker] * (1 - c)
    for o in orders:
        if o.side == "BUY":
            cash -= o.qty * prices[o.ticker] * (1 + c)
            positions[o.ticker] = positions.get(o.ticker, 0.0) + o.qty
    if cash < -_TOL:
        reasons.append(f"현금 음수 방지: 체결 후 현금 {cash:.2f} USD")

    # 단일 종목 상한 (성장주) — 체결 후 상태 기준.
    # 수수료가 NAV 를 깎아 목표 10% 가 산술적으로 10.0X% 로 보이는 드래그는
    # 위반이 아니다. 최대 회전율(전량 매도+매수 = 2×NAV) 기준 상계: cap×(1+2c).
    nav_after = cash + sum(q * prices[t] for t, q in positions.items())
    if nav_after > 0:
        growth_tickers = set(bucket_cfg["sleeves"].get("GROWTH", {}))
        cap = bucket_cfg["max_single_stock"] * (1 + 2 * c)
        for t in growth_tickers:
            w = positions.get(t, 0.0) * prices.get(t, 0.0) / nav_after
            if w > cap + _TOL:
                reasons.append(
                    f"단일 종목 상한 위반: {t} {w:.2%} > 허용 {cap:.2%} "
                    f"(상한 {bucket_cfg['max_single_stock']:.0%} + 수수료 상계)")

    return GateResult(not reasons, tuple(reasons))
