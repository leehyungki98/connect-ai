"""종목별 매입원가·평가 계산 — 브로커 앱 스타일 표시용 (표시 전용, 매매 무관).

매입원가(cost basis)는 ledger/rebalances/*.json 의 체결 기록에서 재구성한다.
평균원가법 — BUY 는 (체결액+수수료)를 원가에 더하고, SELL 은 보유 비율만큼 원가를 덜어낸다.
이게 유일한 진실 원천이다: holdings.json 은 수량만 갖고 원가는 안 갖는다.
"""
import json
from pathlib import Path

_EPS = 1e-9


def cost_basis(rebalances_dir) -> dict:
    """티커별 평균 매입원가(수수료 포함) + 보유수량.

    반환: {ticker: {"shares": float, "cost_usd": float}} (보유 0 은 제외).
    superseded/ 하위(폐기된 설립 기록)는 최상위 *.json 만 읽으므로 자동 제외된다.
    """
    d = Path(rebalances_dir)
    files = sorted(p for p in d.glob("*.json"))   # 최상위만 — superseded/ 안 읽음
    acc = {}
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for fill in data.get("fills", []):
            t = fill["ticker"]
            qty = float(fill["qty"])
            a = acc.setdefault(t, {"shares": 0.0, "cost": 0.0})
            if fill["side"] == "BUY":
                a["cost"] += float(fill["gross_usd"]) + float(fill["commission_usd"])
                a["shares"] += qty
            else:  # SELL — 보유 비율만큼 원가 차감 (평균원가법)
                if a["shares"] > _EPS:
                    a["cost"] -= a["cost"] * (qty / a["shares"])
                a["shares"] -= qty
    return {t: {"shares": round(v["shares"], 6), "cost_usd": round(v["cost"], 2)}
            for t, v in acc.items() if v["shares"] > _EPS}


def evaluate(shares: float, price: float, cost_usd: float, usdkrw: float) -> dict:
    """한 종목 평가 — 브로커 앱 컬럼(평가금액·평가손익·손익률·매입금액)."""
    value_usd = shares * price
    pnl_usd = value_usd - cost_usd
    ret = (pnl_usd / cost_usd) if cost_usd > _EPS else 0.0
    return {
        "shares": round(shares, 6),
        "price": round(price, 2),
        "value_usd": round(value_usd, 2),
        "cost_usd": round(cost_usd, 2),
        "pnl_usd": round(pnl_usd, 2),
        "ret_pct": round(ret * 100, 2),
        "value_krw": round(value_usd * usdkrw),
        "cost_krw": round(cost_usd * usdkrw),
        "pnl_krw": round(pnl_usd * usdkrw),
    }
