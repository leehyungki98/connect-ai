"""미장팀 카드 평가 1회 갱신 — positions_view.json 만 쓴다 (원장 무접촉).

왜 run_daily 를 15분마다 돌리지 않는가: run_daily 는 nav_log 에 append 하고
ledger 미러까지 건드린다. 15분마다 돌리면 관찰 원장이 하루 수십 줄로 오염된다.
NAV 시계열은 '하루 한 점'이어야 의미가 있다.

그래서 이 스크립트는 표시용 값만 다시 계산한다 — 읽기: holdings.json + 시세,
쓰기: positions_view.json 뿐. 전략·주문·관찰기록 어디에도 영향 없다.

--live: 미국장 중이면 준실시간(1분봉 마지막). 기본은 일봉 종가.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longcore import positions, schedule, store  # noqa: E402
from longcore.clock import us_eastern_now  # noqa: E402
from longcore.config import BUCKETS, FX_TICKER  # noqa: E402
from longcore.data import fetch_history  # noqa: E402

import pandas as pd  # noqa: E402


def _us_market_open() -> bool:
    """미국 정규장 여부 (동부 09:30~16:00, 평일). 서머타임은 tz 가 처리한다."""
    now = us_eastern_now()
    if now.weekday() > 4:
        return False
    mins = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= mins <= 16 * 60


def _live_prices(tickers: list) -> dict:
    """준실시간 — yfinance 1분봉 마지막값. 실패한 티커는 빼고 반환(호출부가 폴백)."""
    import yfinance as yf
    out = {}
    for t in tickers:
        try:
            df = yf.Ticker(t).history(period="1d", interval="1m")
            if not df.empty:
                out[t] = float(df["Close"].dropna().iloc[-1])
        except Exception:  # noqa: BLE001
            continue
    return out


def main() -> int:
    live = "--live" in sys.argv and _us_market_open()
    holdings = store.load_holdings()
    for bucket, cfg in BUCKETS.items():
        holding = holdings.get(bucket)
        if not holding or not holding.get("initialized"):
            continue
        tickers = sorted(holding["positions"])
        start = (pd.Timestamp.today() - pd.Timedelta(days=30)).date().isoformat()
        hist = fetch_history(tickers + [FX_TICKER], start).ffill()
        asof = hist.index[-1].date()
        prices = {t: float(hist[t].iloc[-1]) for t in tickers}
        usdkrw = float(hist[FX_TICKER].iloc[-1])
        session = "종가"
        if live:
            lp = _live_prices(tickers)
            if len(lp) == len(tickers):     # 일부만 받으면 섞지 않는다 (기준 시점 불일치)
                prices, session = lp, "장중"

        cb = positions.cost_basis(store.LEDGER_DIR / "rebalances")
        rows, tot_val, tot_cost = [], 0.0, 0.0
        for sym, pos in holding["positions"].items():
            cost = cb.get(sym, {}).get("cost_usd", 0.0)
            ev = positions.evaluate(pos["shares"], prices[sym], cost, usdkrw)
            ev["ticker"] = sym
            rows.append(ev)
            tot_val += ev["value_usd"]
            tot_cost += ev["cost_usd"]
        rows.sort(key=lambda r: -r["value_usd"])
        cash = holding["cash_usd"]
        from datetime import date as _date
        view = {
            "asof": asof.isoformat(), "session": session,
            # 분기 점검일 카운트다운 (D-7 부터, 창 밖이면 None). 표시 전용 추정이며
            # 체결 허가는 rebalance_gate 가 실제 거래일로 판단한다.
            "rebalance_dday": schedule.days_until_check(_date.today()),
            "rebalance_date": schedule.next_check_day(_date.today()).isoformat(),
            "rebalance_notice": schedule.countdown_line(_date.today()),
            "updated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
            "usdkrw": round(usdkrw, 2), "cash_usd": round(cash, 2),
            "cash_krw": round(cash * usdkrw), "positions": rows,
            "total": {
                "value_usd": round(tot_val + cash, 2), "cost_usd": round(tot_cost, 2),
                "pnl_usd": round(tot_val - tot_cost, 2),
                "ret_pct": round((tot_val - tot_cost) / tot_cost * 100, 2) if tot_cost else 0.0,
                "value_krw": round((tot_val + cash) * usdkrw),
            },
        }
        (store.STATE_DIR / "positions_view.json").write_text(
            json.dumps(view, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{bucket}] {asof} ({session}) ₩{view['total']['value_krw']:,} "
              f"({view['total']['ret_pct']:+.2f}%) · 환율 {usdkrw:,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
