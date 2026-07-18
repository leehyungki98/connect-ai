"""백테스트 실행 (pykrx 실데이터).

사용법: python scripts/run_backtest.py [--start YYYYMMDD] [--end YYYYMMDD]
기본: 최근 거래일 기준 직전 1년. 유니버스는 start 시점 기준으로 고정
(생존 편향을 완전히 제거하진 못함 — 결과 해석 시 유의).
필요: .env에 KRX_ID/KRX_PW (pykrx 로그인).
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autotrader.backtest import Bar, run_backtest
from autotrader.config import INITIAL_CAPITAL_KRW, STATE_DIR
from autotrader.screener.universe import REQUEST_DELAY_SEC, fetch_universe
from smoke_kis import load_env

WARMUP_DAYS = 130  # 달력일 기준 여유 (거래일 61개 확보용)


def export_krx_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        env = load_env(env_file)
        for k in ("KRX_ID", "KRX_PW"):
            if env.get(k):
                os.environ.setdefault(k, env[k])


def fetch_bars(symbols, start, end):
    from pykrx import stock

    fetch_start = (
        dt.datetime.strptime(start, "%Y%m%d") - dt.timedelta(days=WARMUP_DAYS)
    ).strftime("%Y%m%d")
    data = {}
    for i, sym in enumerate(symbols):
        time.sleep(REQUEST_DELAY_SEC)  # KRX 연속조회 차단 회피
        df = stock.get_market_ohlcv(fetch_start, end, sym)
        if df.empty:
            continue
        data[sym] = [
            Bar(idx.date().isoformat(), int(r["시가"]), int(r["고가"]),
                int(r["저가"]), int(r["종가"]), int(r["거래량"]))
            for idx, r in df.iterrows()
            if int(r["거래량"]) > 0  # 휴장/정지일 제외
        ]
        if (i + 1) % 50 == 0:
            print(f"  ...{i + 1}/{len(symbols)} 종목")
    return data


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start")
    p.add_argument("--end")
    args = p.parse_args()

    export_krx_env()
    from pykrx import stock

    end = args.end or stock.get_nearest_business_day_in_a_week()
    start = args.start or (
        dt.datetime.strptime(end, "%Y%m%d") - dt.timedelta(days=365)
    ).strftime("%Y%m%d")

    print(f"[backtest] 기간 {start} ~ {end}, 유니버스 선정(asof={start})...")
    universe = fetch_universe(asof=start)
    symbols = [s.symbol for s in universe]
    print(f"[backtest] {len(symbols)}종목 일봉 수집 (약 {len(symbols) * 0.3:.0f}초)...")
    data = fetch_bars(symbols, start, end)

    print(f"[backtest] 실행: {len(data)}종목")
    r = run_backtest(data, INITIAL_CAPITAL_KRW)

    win_rate = r.wins / r.trades if r.trades else 0.0
    print(f"  트레이드     : {r.trades} (승률 {win_rate:.0%})")
    print(f"  총수익률     : {r.total_return:+.2%} (관찰용)")
    print(f"  MDD          : {r.mdd:.2%}")
    print(f"  샤프(연환산) : {r.sharpe:+.2f}")

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    out = STATE_DIR / f"backtest_{start}_{end}.json"
    out.write_text(json.dumps({
        "start": start, "end": end, "symbols": len(data),
        "trades": r.trades, "wins": r.wins, "total_return": r.total_return,
        "mdd": r.mdd, "sharpe": r.sharpe, "equity_curve": list(r.equity_curve),
    }, ensure_ascii=False), encoding="utf-8")
    print(f"[backtest] 저장: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
