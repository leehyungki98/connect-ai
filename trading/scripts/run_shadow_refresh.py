"""섀도 평가 1회 갱신 — 준실시간 KR 시세로 shadow_view.json 만 쓴다 (브라우저·루프 없음).

대시보드가 15분마다 이걸 백그라운드로 돌려 카드 평가금액을 최신으로 유지한다.
순수 뷰어 — 주문·기록·실매매 무영향 (shadow_state 읽기 + shadow_view.json 쓰기만).
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from autotrader import shadow  # noqa: E402
from watch_shadow_live import VIEW_JSON, _build_view, _prices  # noqa: E402


def _close_prices(symbols: list) -> dict:
    """공식 종가 (pykrx 일봉). 장중에 쓰는 yfinance 는 종가로 못 쓴다 —
    마감 후에도 마지막 1분봉이 종가와 다르게 남는다 (실측: 하나금융 133,300 vs 종가 132,600).
    돈이 걸린 숫자라 마감 스냅샷은 거래소 확정값만 쓴다. 못 받으면 빈 dict → 호출부가 폴백."""
    from smoke_kis import load_env
    if (ROOT / ".env").exists():
        env = load_env(ROOT / ".env")
        for k in ("KRX_ID", "KRX_PW"):
            if env.get(k):
                os.environ.setdefault(k, env[k])
    from pykrx import stock
    today = date.today().strftime("%Y%m%d")
    out = {}
    for sym in symbols:
        try:
            df = stock.get_market_ohlcv(today, today, sym)
            if not df.empty:
                out[sym] = int(df["종가"].iloc[-1])
        except Exception:  # noqa: BLE001
            continue
    return out


def main() -> int:
    # --close: 장 마감 후 1회. 값은 15:30 종가라 '장중'이 아니라 '종가'로 표시한다.
    session = "종가" if "--close" in sys.argv else "장중"
    state = shadow.load_state()
    syms = list(state.get("positions", {}))
    prices = {}
    if syms and session == "종가":
        try:
            prices = _close_prices(syms)
        except Exception as e:  # noqa: BLE001
            print(f"  종가 조회 실패 — 준실시간으로 대체 ({e})")
    if syms and not prices:
        prices = _prices(syms)
        if session == "종가":
            session = "장중"   # 확정 종가를 못 받았으면 종가라고 표시하지 않는다
    view = _build_view(state, prices, session=session)
    VIEW_JSON.parent.mkdir(parents=True, exist_ok=True)
    VIEW_JSON.write_text(json.dumps(view, ensure_ascii=False), encoding="utf-8")
    print(f"섀도 평가 갱신({session}) — 보유 {len(syms)}종목 · "
          f"₩{view['total_value_krw']:,} ({view['total_ret_pct']:+.2f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
