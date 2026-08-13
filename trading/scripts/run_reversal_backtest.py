"""약세장 모멘텀 반전 가설 검증 (연구용 — 라이브 전략 무접촉).

현빈이 섀도 22건에서 잡은 가설: "폭 낮을 때 상위 순위(센 종목)가 하위보다 더 깨진다."
이걸 과거 일봉 수백일로 확인한다. 매수/전략을 바꾸지 않는다 — 순수 신호 측정이다.

방법: 매 거래일 랭킹(rank)과 시장 폭을 계산하고, 각 순위 종목의 '앞으로 H일'
수익률을 기록 → (폭 구간 × 순위대) 로 평균·승률 집계. 상위(1~3)가 하위(6+)보다
약세장에서 실제로 나쁜지 본다.

실행: python scripts/run_reversal_backtest.py [--days 400] [--horizon 10]
필요: .env 의 KRX_ID/KRX_PW (pykrx).
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from autotrader.screener.ranking import MIN_HISTORY, SymbolData, rank  # noqa: E402


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return 0.0 if not n else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=400, help="조회 캘린더 일수")
    ap.add_argument("--horizon", type=int, default=10, help="앞으로 며칠 수익률")
    ap.add_argument("--top-n", type=int, default=10)
    args = ap.parse_args()

    from run_backtest import export_krx_env, fetch_bars
    from autotrader.screener.universe import fetch_universe
    import pandas as pd

    export_krx_env()
    end = pd.Timestamp.today().date().isoformat()
    start = (pd.Timestamp.today() - pd.Timedelta(days=args.days)).date().isoformat()
    print(f"유니버스 조회 (asof {start}) …")
    universe_syms = [s.symbol if hasattr(s, "symbol") else s
                     for s in fetch_universe(asof=start)]
    print(f"  {len(universe_syms)}종목 · 일봉 조회(수 분 소요) …")
    data = fetch_bars(universe_syms, start, end)          # {sym: [Bar,...]}

    dates = sorted({b.date for bars in data.values() for b in bars})
    by_date = {sym: {b.date: b for b in bars} for sym, bars in data.items()}
    closes: dict = {sym: [] for sym in data}

    # 버킷: (폭구간, 순위대) → 앞으로 H일 수익률 리스트
    buckets = defaultdict(list)
    date_close = {sym: {b.date: b.close for b in bars} for sym, bars in data.items()}

    for t, d in enumerate(dates):
        universe = [SymbolData(sym, tuple(h)) for sym, h in closes.items()
                    if len(h) >= MIN_HISTORY]
        ranked = rank(universe, args.top_n) if universe else []

        # 시장 폭 (t까지의 종가로 — 오늘 기준)
        elig = [h for h in closes.values() if len(h) >= 20]
        breadth = (sum(1 for h in elig if h[-1] > sum(h[-20:]) / 20) / len(elig)
                   if elig else None)

        # 앞으로 H일 수익률 (미래 일봉이 있을 때만)
        fut_i = t + args.horizon
        if breadth is not None and fut_i < len(dates):
            fd = dates[fut_i]
            for i, r in enumerate(ranked):
                c0 = date_close.get(r.symbol, {}).get(d)
                c1 = date_close.get(r.symbol, {}).get(fd)
                if c0 and c1:
                    rank_tier = "1~3" if i < 3 else ("4~5" if i < 5 else "6+")
                    br_tier = ("폭<30" if breadth < 0.30 else
                               "폭30~50" if breadth < 0.50 else "폭>=50")
                    buckets[(br_tier, rank_tier)].append((c1 / c0 - 1) * 100)

        # 오늘 종가를 이력에 추가 (다음 날 랭킹용)
        for sym in closes:
            bar = by_date[sym].get(d)
            if bar is not None:
                closes[sym].append(bar.close)

    # 리포트
    print(f"\n=== 폭 구간 × 순위대별 앞으로 {args.horizon}일 수익률 ===")
    print(f"{'폭구간':>9} {'순위':>5} {'표본':>6} {'평균':>8} {'중앙값':>8} {'승률':>6}")
    for br in ("폭<30", "폭30~50", "폭>=50"):
        for rk in ("1~3", "4~5", "6+"):
            xs = buckets.get((br, rk), [])
            if not xs:
                continue
            avg = sum(xs) / len(xs)
            wr = sum(1 for x in xs if x > 0) / len(xs) * 100
            print(f"{br:>9} {rk:>5} {len(xs):>6} {avg:>+7.2f}% {_median(xs):>+7.2f}% {wr:>5.0f}%")

    # 가설 판정 — 폭<30 에서 상위(1~3) vs 하위(6+)
    top = buckets.get(("폭<30", "1~3"), [])
    low = buckets.get(("폭<30", "6+"), [])
    print("\n=== 가설 판정 (폭<30% 구간) ===")
    if len(top) >= 30 and len(low) >= 30:
        ta, la = sum(top) / len(top), sum(low) / len(low)
        print(f"  상위 1~3등: 평균 {ta:+.2f}% (n={len(top)})")
        print(f"  하위 6등+ : 평균 {la:+.2f}% (n={len(low)})")
        if la - ta >= 1.0:
            print(f"  → ✅ 반전 확인: 약세장에서 하위가 {la - ta:.1f}%p 낫다. "
                  "섀도 22건 신호가 과거 데이터로도 재현됨.")
        elif ta - la >= 1.0:
            print(f"  → ❌ 반전 아님: 오히려 상위가 {ta - la:.1f}%p 낫다. 섀도 신호는 우연일 가능성.")
        else:
            print("  → ⚪ 차이 미미. 결론 유보 — 더 긴 기간 필요.")
    else:
        print(f"  표본 부족(상위 {len(top)}·하위 {len(low)}, 각 30 필요) — 기간을 늘려라(--days).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
