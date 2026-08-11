"""미장팀 보유 준실시간 관찰 — 브라우저에서 평가금액 곡선을 본다.

⚠ 순수 뷰어다. 데스크 전략(일봉 종가 기반)을 바꾸지 않고, nav_log/ledger/관찰
기록에 손대지 않는다 (holdings·ledger 읽기만). 매매도 없다. 사용자가 '보고
싶어서' 만든 도구 — yfinance 무료 시세는 약 15분 지연이라 준실시간이다.

사용: python scripts/watch_live.py            # 60초 간격, 브라우저 자동 오픈
      python scripts/watch_live.py --interval 30
장 마감 중엔 마지막 종가로 평평하게 나온다(정상). Ctrl-C 로 종료.
정중한 폴링: 기본 60초. 너무 짧게(수 초) 돌리지 말 것.
"""
import argparse
import json
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longcore import live, positions, store  # noqa: E402
from longcore.clock import is_market_open, us_eastern_now  # noqa: E402
from longcore.config import BUCKETS, FX_TICKER  # noqa: E402

LIVE_HTML = store.STATE_DIR / "live.html"
LIVE_JSON = store.STATE_DIR / "live_intraday.json"


def _prices(tickers):
    """준실시간 시세 — 1분봉 마지막 유효값. 장 마감 중이면 마지막 종가."""
    import yfinance as yf
    df = yf.download(tickers, period="2d", interval="1m",
                     auto_adjust=True, progress=False)
    close = df["Close"]
    import pandas as pd
    if isinstance(close, pd.Series):
        close = close.to_frame(name=tickers[0])
    close = close.ffill()
    last = close.iloc[-1]
    return {t: float(last[t]) for t in tickers}


def _load_points(date_str):
    if LIVE_JSON.exists():
        try:
            d = json.loads(LIVE_JSON.read_text(encoding="utf-8"))
            if d.get("date") == date_str:
                return d.get("points", [])
        except (OSError, ValueError):
            pass
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=60, help="폴링 간격(초), 기본 60")
    ap.add_argument("--bucket", default="해외증권")
    args = ap.parse_args()
    interval = max(15, args.interval)   # 하한 15초 (정중한 폴링)

    holdings = store.load_holdings()
    holding = holdings.get(args.bucket)
    if not holding or not holding.get("initialized"):
        print(f"[{args.bucket}] 미초기화 — run_rebalance.py --init 먼저.")
        return 1

    cfg = BUCKETS[args.bucket]
    tickers = sorted(holding["positions"])
    cb = positions.cost_basis(store.LEDGER_DIR / "rebalances")
    cost_total = sum(cb.get(t, {}).get("cost_usd", 0.0) for t in tickers)

    print(f"준실시간 관찰 시작 — {interval}초 간격. 브라우저: {LIVE_HTML}")
    print("Ctrl-C 로 종료. (전략·기록에 영향 없음 — 순수 뷰어)")
    opened = False
    try:
        while True:
            et = us_eastern_now()
            day = et.date().isoformat()
            prices = _prices(tickers + [FX_TICKER])
            usdkrw = prices.pop(FX_TICKER)

            hlist, total_val = [], 0.0
            for t in tickers:
                sh = holding["positions"][t]["shares"]
                ev = positions.evaluate(sh, prices[t], cb.get(t, {}).get("cost_usd", 0.0), usdkrw)
                ev["ticker"] = t
                hlist.append(ev)
                total_val += ev["value_usd"]
            hlist.sort(key=lambda h: -h["value_usd"])
            cash = holding["cash_usd"]
            total_usd = total_val + cash

            points = _load_points(day)
            points.append({"t": et.strftime("%H:%M"), "total_usd": round(total_usd, 2)})
            LIVE_JSON.write_text(json.dumps({"date": day, "points": points},
                                            ensure_ascii=False), encoding="utf-8")

            state = {
                "date": day, "updated": et.strftime("%Y-%m-%d %H:%M ET"),
                "usdkrw": usdkrw, "cash_usd": cash,
                "total_usd": total_usd,
                "total_pnl_usd": total_val - cost_total,
                "total_ret_pct": (total_val - cost_total) / cost_total * 100 if cost_total else 0.0,
                "holdings": hlist, "points": points,
                "note": "" if is_market_open(et) else "장 마감 — 마지막 종가",
            }
            LIVE_HTML.write_text(live.render_html(state, interval), encoding="utf-8")

            if not opened:
                webbrowser.open(LIVE_HTML.resolve().as_uri())
                opened = True
            state_flag = "장중" if is_market_open(et) else "마감"
            print(f"  {et.strftime('%H:%M:%S')} ET [{state_flag}] "
                  f"평가 ${total_usd:,.2f} ({state['total_ret_pct']:+.2f}%) · 점 {len(points)}개")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n종료. (live.html 은 마지막 상태로 남아 있음)")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
