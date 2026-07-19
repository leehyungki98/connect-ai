"""백테스트 실행 (pykrx 실데이터).

사용법:
  python scripts/run_backtest.py [--start YYYYMMDD] [--end YYYYMMDD]
  python scripts/run_backtest.py --compare   ← P2/P1/P3 개선 변형 누적 비교 (기간 분할)

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
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows 콘솔(cp949) 출력 크래시 방지
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

from autotrader.backtest import (
    ADOPTED_PARAMS,
    BacktestResult,
    BaselineParams,
    Bar,
    run_backtest,
)
from autotrader.config import INITIAL_CAPITAL_KRW, STATE_DIR
from autotrader.screener.universe import REQUEST_DELAY_SEC, fetch_universe
from smoke_kis import load_env

WARMUP_DAYS = 130  # 달력일 기준 여유 (거래일 61개 확보용)

# 독립 검증용 변형 (2026-07-19 미션 채택 후보 — 검증 연도에서 재튜닝 금지)
# 이전 탐색용 V1~V3은 미션 스윕(run_sweep.py)으로 대체됨.
VARIANTS = [
    ("V0 기준", BaselineParams()),
    ("C1 cd10/b.5/n2", BaselineParams(cooldown_bars=10,
                                      market_breadth_min=0.5, max_new_per_day=2)),
    ("C2 +volk2.5", BaselineParams(cooldown_bars=10, stop_vol_k=2.5,
                                   market_breadth_min=0.5, max_new_per_day=2)),
]


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


def summarize(r: BacktestResult) -> dict:
    stops = [x for x in r.trade_log if x.exit_kind == "stop"]
    return {
        "ret": r.total_return, "mdd": r.mdd, "sharpe": r.sharpe,
        "trades": r.trades,
        "win": (r.wins / r.trades) if r.trades else 0.0,
        "stop_share": (len(stops) / len(r.trade_log)) if r.trade_log else 0.0,
    }


def print_result(r: BacktestResult):
    win_rate = r.wins / r.trades if r.trades else 0.0
    print(f"  트레이드     : {r.trades} (승률 {win_rate:.0%})")
    print(f"  총수익률     : {r.total_return:+.2%} (관찰용)")
    print(f"  MDD          : {r.mdd:.2%}")
    print(f"  샤프(연환산) : {r.sharpe:+.2f}")

    if r.trade_log:
        print("  [청산 사유별]")
        for kind in ("stop", "target", "time"):
            rows = [x for x in r.trade_log if x.exit_kind == kind]
            if not rows:
                continue
            k_wins = sum(1 for x in rows if x.pnl_krw > 0)
            avg_pct = sum(x.pnl_pct for x in rows) / len(rows)
            total = sum(x.pnl_krw for x in rows)
            print(f"    {kind:6s}: {len(rows):3d}건, 승률 {k_wins / len(rows):4.0%}, "
                  f"평균 {avg_pct:+.2%}, 합계 {total:+,}원")
        print("  [최악 트레이드 5]")
        for x in sorted(r.trade_log, key=lambda y: y.pnl_krw)[:5]:
            print(f"    {x.symbol} {x.entry_date}→{x.exit_date} {x.exit_kind:6s} "
                  f"{x.bars_held:2d}일 {x.pnl_pct:+.1%} ({x.pnl_krw:+,}원)")


def run_compare(data: dict) -> dict:
    """변형 4종 × 기간 분할(전/후반) 비교. 반환: 요약 dict (JSON 저장용)."""
    dates = sorted({b.date for bars in data.values() for b in bars})
    mid = dates[len(dates) // 2]
    halves = [
        ("전반", {s: [b for b in bs if b.date <= mid] for s, bs in data.items()}),
        ("후반", {s: [b for b in bs if b.date > mid] for s, bs in data.items()}),
    ]
    out = {}
    print(f"\n[compare] 분할 기준일: {mid} (각 절반은 워밍업 61거래일 이후부터 매매)")
    print(f"  {'변형':14s} {'구간':4s} {'수익률':>8s} {'MDD':>7s} {'샤프':>6s} "
          f"{'횟수':>5s} {'승률':>5s} {'stop비중':>8s}")
    for name, params in VARIANTS:
        for half_name, half_data in halves:
            r = run_backtest(half_data, INITIAL_CAPITAL_KRW, params)
            s = summarize(r)
            out[f"{name}|{half_name}"] = s
            print(f"  {name:14s} {half_name:4s} {s['ret']:+8.2%} {s['mdd']:7.2%} "
                  f"{s['sharpe']:+6.2f} {s['trades']:5d} {s['win']:5.0%} "
                  f"{s['stop_share']:8.0%}")
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--compare", action="store_true",
                   help="P2/P1/P3 변형 누적 비교 (기간 분할)")
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

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    if args.compare:
        summary = run_compare(data)
        out = STATE_DIR / f"backtest_compare_{start}_{end}.json"
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        print(f"[backtest] 저장: {out}")
        return 0

    print("[backtest] 채택 파라미터(C2)로 실행 — V0 비교는 --compare 사용")
    r = run_backtest(data, INITIAL_CAPITAL_KRW, ADOPTED_PARAMS)
    print_result(r)
    out = STATE_DIR / f"backtest_{start}_{end}.json"
    out.write_text(json.dumps({
        "start": start, "end": end, "symbols": len(data),
        "trades": r.trades, "wins": r.wins, "total_return": r.total_return,
        "mdd": r.mdd, "sharpe": r.sharpe, "equity_curve": list(r.equity_curve),
        "trade_log": [asdict(t) for t in r.trade_log],
    }, ensure_ascii=False), encoding="utf-8")
    print(f"[backtest] 저장: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
