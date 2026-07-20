"""거래비용 민감도 — "자본 규모가 결과를 바꾸는가" 에 답한다.

2026-07-20 검증에서 $5,068 과 $100,000 결과가 완전히 동일하게 나왔다. 그건 전략이
규모에 강건해서가 아니라 **모델에 고정비가 없어서**였다. 이 스크립트는 고정비
(건당 최소 수수료)와 이산화(정수 주)를 넣고 규모 효과를 실제로 측정한다.

읽는 법: 실계좌 전환 시 사용자의 증권사 조건이 어느 칸에 해당하는지 찾고,
그 칸의 비용이 감당 가능한지 판단한다. 감당 불가면 자본을 키우거나 조건을 바꾼다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longcore.config import (BACKTEST_END, BACKTEST_START, BENCHMARK,  # noqa: E402
                             BUCKETS)
from longcore.data import fetch_history  # noqa: E402

from run_backtest import metrics, simulate  # noqa: E402

CAPITALS = [5_068, 20_000, 100_000]          # 실제 자본 / 4배 / 20배
MIN_COMMISSIONS = [0.0, 0.5, 1.0, 2.0]       # 증권사 건당 최소 수수료 시나리오


def main() -> int:
    cfg = BUCKETS["해외증권"]
    tickers = sorted({t for comp in cfg["sleeves"].values() for t in comp})
    px = fetch_history(tickers + [BENCHMARK], BACKTEST_START, BACKTEST_END).dropna()
    print(f"구간 {px.index[0].date()} ~ {px.index[-1].date()}\n")

    print("── ① 소수점 매매 가능 (현재 가정) ─────────────────────────────")
    print(f"{'자본':>10} {'최소수수료':>10} {'CAGR':>8} {'누적':>10} "
          f"{'총비용':>10} {'자본대비':>9}")
    base = {}
    for cap in CAPITALS:
        for mc in MIN_COMMISSIONS:
            nav, ev, cost = simulate(px, cfg, cap, min_commission=mc,
                                     fractional=True)
            m = metrics(nav)
            if mc == 0.0:
                base[cap] = m["CAGR"]
            print(f"{cap:>10,.0f} {mc:>10.2f} {m['CAGR']:>8.2%} "
                  f"{m['total']:>10.2%} {cost:>10,.2f} {cost/cap:>9.2%}")
        print()

    print("── ② 정수 주만 가능 ────────────────────────────────────────")
    print(f"{'자본':>10} {'CAGR':>8} {'누적':>10} {'vs 소수주':>10}  비고")
    for cap in CAPITALS:
        nav, ev, cost = simulate(px, cfg, cap, min_commission=0.0,
                                 fractional=False)
        m = metrics(nav)
        gap = m["CAGR"] - base[cap]
        note = _viability_note(px, cfg, cap)
        print(f"{cap:>10,.0f} {m['CAGR']:>8.2%} {m['total']:>10.2%} "
              f"{gap:>+10.2%}  {note}")

    print("  ※ ② 의 CAGR 차이는 신호가 아니라 잡음이다 — 정수 주는 목표에 미달하게")
    print("     사는 쪽이라 우연히 유리해 보일 수 있다. 판단은 ③ 으로 한다.")

    print("\n── ③ 데스크 성립 가능성 (현재가·정수 주 기준) ────────────────")
    _check_viability(px, cfg)
    print("\n  '성립 불가' 는 수익률 문제가 아니라 **구조 문제**다 — 목표 비중을")
    print("  애초에 만들 수 없다는 뜻이다. 소수점 매매는 선호가 아니라 요건이다.")
    return 0


def _viability_note(px, cfg, cap) -> str:
    """가장 비싼 종목 1주가 NAV 에서 차지하는 비중 — **현재 가격 기준**.

    구간 시작(2016) 가격을 쓰면 안 된다. VOO 가 $157 이던 시절 기준으로는
    문제없어 보이지만 오늘은 $683 이라 그림이 완전히 달라진다.
    """
    last = px.index[-1]
    tickers = sorted({t for c in cfg["sleeves"].values() for t in c})
    worst = max(tickers, key=lambda t: float(px.loc[last, t]))
    return f"현재가 기준 {worst} 1주 = NAV의 {float(px.loc[last, worst]) / cap:.1%}"


def _check_viability(px, cfg) -> None:
    """현재 시점 가격으로, 정수 주 배분이 ±5%p 밴드를 만족하는지 직접 계산."""
    last = px.index[-1]
    for cap in CAPITALS:
        prices = {t: float(px.loc[last, t]) for t in
                  sorted({t for c in cfg["sleeves"].values() for t in c})}
        ok, detail = _fits_band(prices, cfg, cap)
        mark = "성립" if ok else "⚠ 성립 불가"
        print(f"  자본 ${cap:>8,.0f} → {mark}  {detail}")


def _fits_band(prices, cfg, cap):
    """각 슬리브를 정수 주로 채웠을 때 목표 대비 이탈이 밴드 안인가."""
    band = cfg["band"]
    worst_name, worst_dev = None, 0.0
    for sleeve, comps in cfg["sleeves"].items():
        target = cfg["targets"][sleeve]
        value = 0.0
        for ticker, w in comps.items():
            want = target * w * cap
            shares = int(want / prices[ticker])
            value += shares * prices[ticker]
        dev = value / cap - target
        if abs(dev) > abs(worst_dev):
            worst_name, worst_dev = sleeve, dev
    return abs(worst_dev) <= band, f"최대 이탈 {worst_name} {worst_dev:+.2%} (밴드 ±{band:.0%})"


if __name__ == "__main__":
    raise SystemExit(main())
