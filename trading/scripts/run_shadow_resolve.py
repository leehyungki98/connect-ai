"""섀도 청산 판정 — 보유 중 섀도 포지션을 최신 일봉으로 손절/목표/기간 청산.

주문 없음. pykrx 일봉으로 백테스트와 동일 잣대(손절→목표→기간)로 판정하고
매도 시각·사유·실현손익을 shadow_trades.jsonl 에 남긴다.
매일(또는 장 마감 후) 1회 돌리면 섀도 포지션이 실매매처럼 청산된다.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from autotrader import shadow  # noqa: E402


def _load_krx_env() -> None:
    """pykrx용 KRX 로그인 — 프리마켓과 동일하게 .env 의 KRX_ID/KRX_PW 주입."""
    from smoke_kis import load_env
    if (ROOT / ".env").exists():
        env = load_env(ROOT / ".env")
        for k in ("KRX_ID", "KRX_PW"):
            if env.get(k):
                os.environ.setdefault(k, env[k])


def _fetch_bars(symbol: str, from_date: str, to_date: str) -> list:
    """pykrx 일봉 (진입일~오늘). 반환: [{date, open, high, low, volume}] 시간순."""
    from pykrx import stock
    fd, td = from_date.replace("-", ""), to_date.replace("-", "")
    df = stock.get_market_ohlcv(fd, td, symbol)
    bars = []
    for idx, row in df.iterrows():
        bars.append({
            "date": idx.strftime("%Y-%m-%d"),
            "open": int(row["시가"]), "high": int(row["고가"]),
            "low": int(row["저가"]), "volume": int(row["거래량"]),
        })
    return bars


def main() -> int:
    from datetime import date
    today = date.today().isoformat()
    state = shadow.load_state()
    open_pos = state.get("positions", {})
    if not open_pos:
        print("보유 중 섀도 포지션 없음 — 청산할 것 없음.")
        return 0

    _load_krx_env()
    print(f"보유 섀도 {len(open_pos)}종목 → 일봉 조회 후 청산 판정")
    bars_by_symbol = {}
    for sym, p in open_pos.items():
        try:
            bars_by_symbol[sym] = _fetch_bars(sym, p["entry_date"], today)
        except Exception as e:  # noqa: BLE001
            print(f"  {sym}: 일봉 조회 실패 — 보유 유지 ({e})")
            bars_by_symbol[sym] = []

    closed = shadow.resolve(bars_by_symbol)
    if not closed:
        print("청산 트리거 없음 — 전부 보유 유지.")
    else:
        for c in closed:
            mark = {"stop": "손절", "target": "목표", "time": "기간"}.get(c["reason"], c["reason"])
            print(f"  청산 {c['symbol']} · {mark} @ {c['exit_price']:,}원 · "
                  f"{c['ret_pct']:+.2f}% · 보유 {c['hold_days']}일 · "
                  f"실현 {c['realized_krw']:+,}원 ({c['entry_date']}→{c['date']})")
    remaining = shadow.load_state().get("positions", {})
    print(f"남은 보유 섀도: {len(remaining)}종목")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
