"""페이퍼 체결 엔진 — 이 데스크의 유일한 체결 경로.

실행일 수정종가로 체결, 수수료 차감, holdings 갱신. 소수 주 허용 (모의 전용 —
정수 주 강제 시 자본 대비 추적오차가 커져 규칙 검증이 흐려진다).
"""
import json
from pathlib import Path

_TOL = 1e-6


def execute(orders, prices: dict, holding: dict, commission_bps: int,
            min_commission_usd: float = 0.0, sec_fee_bps: float = 0.0):
    """SELL 먼저 → BUY. 반환: (새 holding, 체결 내역). 현금 음수면 예외 (가드가
    먼저 막지만 이중 방어).

    비용 = max(정률 수수료, 건당 최소 수수료) + SEC 수수료(매도만).
    최소 수수료가 **자본 규모에 대한 유일한 고정비**다 — 이게 없으면 백테스트가
    규모에 반응하지 않는다 (2026-07-20 검증에서 드러난 결함).
    """
    c = commission_bps / 10_000.0
    sec = sec_fee_bps / 10_000.0
    cash = holding["cash_usd"]
    positions = {t: dict(p) for t, p in holding.get("positions", {}).items()}
    fills = []

    def fill(o, price):
        gross = o.qty * price
        commission = max(gross * c, min_commission_usd)
        if o.side == "SELL":
            commission += gross * sec        # SEC 수수료는 매도에만
        fills.append({
            "ticker": o.ticker, "side": o.side, "sleeve": o.sleeve,
            "qty": round(o.qty, 6), "price": price,
            "gross_usd": round(gross, 2), "commission_usd": round(commission, 2),
            "min_applied": gross * c < min_commission_usd,
        })
        return gross, commission

    for o in sorted(orders, key=lambda o: 0 if o.side == "SELL" else 1):
        price = prices[o.ticker]
        if o.side == "SELL":
            have = positions.get(o.ticker, {}).get("shares", 0.0)
            if o.qty > have + _TOL:
                raise ValueError(f"{o.ticker}: 보유({have}) 초과 매도({o.qty})")
            gross, commission = fill(o, price)
            cash += gross - commission
            remaining = have - o.qty
            if remaining <= _TOL:
                positions.pop(o.ticker, None)
            else:
                positions[o.ticker]["shares"] = remaining
        else:
            gross, commission = fill(o, price)
            cash -= gross + commission
            positions.setdefault(o.ticker, {"shares": 0.0})
            positions[o.ticker]["shares"] += o.qty
    if cash < -_TOL:
        raise ValueError(f"체결 후 현금 음수: {cash:.2f} USD")

    new_holding = dict(holding)
    new_holding["cash_usd"] = cash
    new_holding["positions"] = positions
    return new_holding, fills


def record_rebalance(ledger_dir, date_str: str, tag: str, reason: str,
                     weights_before: dict, weights_after: dict,
                     nav_before: float, nav_after: float, fills: list) -> Path:
    """ledger/rebalances/ 에 전후 비중·사유·체결 기록 (학습 자산 — git 추적)."""
    ledger_dir = Path(ledger_dir)
    ledger_dir.mkdir(parents=True, exist_ok=True)
    path = ledger_dir / f"{date_str}_{tag}.json"
    payload = {
        "date": date_str,
        "tag": tag,
        "reason": reason,
        "nav_before_usd": round(nav_before, 2),
        "nav_after_usd": round(nav_after, 2),
        "weights_before": {k: round(v, 6) for k, v in weights_before.items()},
        "weights_after": {k: round(v, 6) for k, v in weights_after.items()},
        "fills": fills,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path
