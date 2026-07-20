"""시장 폭 필터의 관망 비용 계측 (관찰 전용 — 전략 변경 아님).

질문: 폭 필터가 막은 날들에, 실제로 얼마를 못 벌었나 / 얼마를 안 잃었나?

방법: 각 거래일 D 에 대해
  1. 그날 시장 폭을 계산 (pipeline._market_breadth 와 동일 정의)
  2. 스크리너가 그날 뽑았을 상위 후보를 재현 (screener.rank — 순수 함수라 재현 가능)
  3. 그 후보들의 H 거래일 뒤 수익률을 측정
  4. 차단된 날 / 열린 날로 나눠 비교

차단된 날의 선도 수익률이 음수면 필터가 값을 하는 것이고,
양수면 관망 비용이 실재하는 것이다. 판단은 하지 않고 숫자만 낸다.

사용법: python scripts/measure_breadth_cost.py [--horizon 10] [--breadth 0.5]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from autotrader.config import STATE_DIR  # noqa: E402
from autotrader.screener.ranking import SymbolData, rank  # noqa: E402

MA_WINDOW = 20


def load_bars(cache: Path) -> dict[str, list[tuple[str, int]]]:
    """{종목: [(날짜, 종가), ...]} — 날짜 오름차순."""
    raw = json.loads(cache.read_text(encoding="utf-8"))["bars"]
    out = {}
    for sym, rows in raw.items():
        out[sym] = sorted((r[0], int(r[4])) for r in rows)
    return out


def breadth_at(bars: dict, idx: dict, di: int) -> float | None:
    """유니버스 중 20일 이평 위 비율. pipeline._market_breadth 와 같은 정의."""
    above = eligible = 0
    for sym, series in bars.items():
        i = idx[sym].get(di)
        if i is None or i + 1 < MA_WINDOW:
            continue
        window = [c for _, c in series[i + 1 - MA_WINDOW: i + 1]]
        eligible += 1
        if series[i][1] > sum(window) / MA_WINDOW:
            above += 1
    return above / eligible if eligible else None


def forward_return(series: list, i: int, horizon: int) -> float | None:
    if i + horizon >= len(series):
        return None
    return series[i + horizon][1] / series[i][1] - 1


def managed_return(bars: list, i: int, horizon: int,
                   stop: float, target: float) -> float | None:
    """손절·목표가를 적용한 수익률 — 전략이 실제로 겪는 값.

    원시 선도수익률은 손절을 무시해서 최악값을 과장한다. 장중 고가/저가로
    먼저 닿는 쪽을 판정하되, 같은 봉에서 둘 다 닿으면 손절을 먼저 본다
    (보수적 가정 — 실제 순서를 일봉으로는 알 수 없다).
    """
    if i + horizon >= len(bars):
        return None
    entry = bars[i][4]
    if entry <= 0:
        return None
    stop_px, target_px = entry * (1 - stop), entry * (1 + target)
    for k in range(i + 1, i + horizon + 1):
        _, _, hi, lo, close, _ = bars[k]
        if lo <= stop_px:
            return -stop
        if hi >= target_px:
            return target
    return bars[i + horizon][4] / entry - 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--horizon", type=int, default=10, help="보유 거래일 수")
    p.add_argument("--breadth", type=float, default=0.5, help="차단 임계값")
    p.add_argument("--top-n", type=int, default=10)
    args = p.parse_args()

    cache = STATE_DIR / "bars_cache.json"
    if not cache.exists():
        print(f"[X] {cache} 없음. 먼저 백테스트 캐시를 만들어주세요.")
        return 2
    bars = load_bars(cache)
    all_dates = sorted({d for s in bars.values() for d, _ in s})
    # 종목별 {날짜인덱스: 시계열 위치}
    date_pos = {d: k for k, d in enumerate(all_dates)}
    idx = {sym: {date_pos[d]: i for i, (d, _) in enumerate(series)}
           for sym, series in bars.items()}

    blocked, opened = [], []
    streak = cur = 0
    max_streak_end = ""
    for di, d in enumerate(all_dates):
        b = breadth_at(bars, idx, di)
        if b is None:
            continue
        # 그날 스크리너가 뽑았을 후보 (해당 시점까지의 데이터만)
        universe = []
        for sym, series in bars.items():
            i = idx[sym].get(di)
            if i is None:
                continue
            universe.append(SymbolData(sym, tuple(c for _, c in series[: i + 1])))
        try:
            picks = rank(universe, args.top_n)
        except ValueError:
            continue
        rets = []
        top1 = None
        for k, r in enumerate(picks):
            i = idx[r.symbol].get(di)
            fr = forward_return(bars[r.symbol], i, args.horizon)
            if fr is None:
                continue
            rets.append(fr)
            if k == 0:
                top1 = fr  # 1순위만 담는 "조건부 예외" 가정
        if not rets:
            continue
        row = (d, b, statistics.mean(rets), top1)
        if b < args.breadth:
            blocked.append(row)
            cur += 1
            if cur > streak:
                streak, max_streak_end = cur, d
        else:
            opened.append(row)
            cur = 0

    def summarize(rows, name, col=2):
        if not rows:
            print(f"  {name}: 표본 없음")
            return
        rs = [r[col] for r in rows if r[col] is not None]
        if not rs:
            print(f"  {name}: 표본 없음")
            return
        wins = sum(1 for x in rs if x > 0)
        print(f"  {name}: {len(rs)}일")
        print(f"    평균 {args.horizon}일 수익률: {statistics.mean(rs):+.2%}")
        print(f"    중앙값:                  {statistics.median(rs):+.2%}")
        print(f"    플러스 비율:              {wins/len(rs):.1%}")
        print(f"    최악:                    {min(rs):+.2%}")

    print(f"[관망 비용 계측] 폭<{args.breadth:.0%} 차단, 상위 {args.top_n}종목, {args.horizon}거래일 보유 가정")
    print(f"  구간: {all_dates[0]} ~ {all_dates[-1]}")
    print("-" * 62)
    summarize(blocked, "차단된 날 (안 샀음)")
    print()
    summarize(opened, "열린 날 (샀음)")
    print()
    print("=" * 62)
    print("[조건부 예외 가정] 차단된 날에 1순위 1종목만 담았다면")
    print("-" * 62)
    summarize(blocked, "차단일 · 1순위만", col=3)
    print()
    summarize(opened, "열린날 · 1순위만", col=3)
    print()
    print(f"  최장 연속 차단: {streak}일 (마지막 {max_streak_end})")
    if blocked and opened:
        diff = statistics.mean([r[2] for r in blocked]) - statistics.mean([r[2] for r in opened])
        print(f"  차단일 − 열린날 평균차(상위 {args.top_n}): {diff:+.2%}")
        print()
        if statistics.mean([r[2] for r in blocked]) < 0:
            print("  → 차단된 날의 후보는 평균적으로 손실. 필터가 값을 하고 있다.")
        else:
            print("  → 차단된 날의 후보도 평균 플러스. 관망 비용이 실재한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
