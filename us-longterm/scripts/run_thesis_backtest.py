"""논지 재판정 규칙의 역사적 검증 — "이 규칙이 바닥에서 팔았을까?"

핵심 질문: NVDA 는 FY2023(2022년 게이밍 붕괴)에 순이익이 3분의 1로 꺾였다.
논지 재판정 규칙이 그때 NVDA 를 퇴출시켰다면 AI 붐 직전 바닥에서 판 것이고,
그 규칙은 포트폴리오를 지키는 게 아니라 죽이는 규칙이다.

⚠ 한계 — 결론을 읽기 전에 반드시 볼 것:
1. yfinance 연간 재무제표는 **5개 연도**뿐이다. 10년 백테스트가 아니라
   "규칙이 죽는 구간이 있었나" 를 보는 **표적 검증**이다.
2. 밸류 기준(선행PER·PEG)은 과거값을 받을 수 없어 **UNKNOWN** 처리된다.
   기준 3개 중 2개만 판정 가능 → 실제 규칙보다 **덜 민감**하다.
   즉 여기서 나온 퇴출 횟수는 **하한**이다 (실제 규칙은 더 자주 퇴출시킨다).
3. 분기 규칙을 연간 데이터에 적용하므로 "2분기 연속" → "2년 연속" 이 된다.
   실제 규칙보다 훨씬 느린 방아쇠다.
4. 실적 발표 시차를 반영한다 — 회계연도 종료 +3개월 이후의 첫 분기 점검일부터
   그 데이터를 쓸 수 있다고 본다 (선견 편향 제거).
5. 종목 선택 편향은 그대로 남아있다 — 오늘 고른 3종을 과거에 적용한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longcore import thesis  # noqa: E402
from longcore.config import (BUCKETS, THESIS_CONSECUTIVE_OUT,  # noqa: E402
                             THESIS_CRITERIA, THESIS_FAIL_THRESHOLD)
from longcore.data import fetch_history  # noqa: E402

import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402

REPORT_LAG_DAYS = 90     # 회계연도 종료 후 실적이 공개되기까지


def annual_fundamentals(ticker: str) -> pd.DataFrame:
    """연도별 순이익률·매출성장·FCF. 밸류 지표는 과거값 부재 → 없음."""
    t = yf.Ticker(ticker)
    inc, cf = t.income_stmt, t.cashflow
    rows = []
    periods = sorted(inc.columns)
    for i, col in enumerate(periods):
        rev = _get(inc, col, "Total Revenue")
        ni = _get(inc, col, "Net Income")
        fcf = _get(cf, col, "Free Cash Flow")
        if rev is None or ni is None:
            continue
        prev_rev = _get(inc, periods[i - 1], "Total Revenue") if i else None
        rows.append({
            "period_end": col,
            "available_from": col + pd.Timedelta(days=REPORT_LAG_DAYS),
            "net_margin": ni / rev,
            "revenue_growth": (rev / prev_rev - 1) if prev_rev else None,
            "free_cashflow": fcf,
            "revenue": rev,
        })
    return pd.DataFrame(rows)


def _get(df, col, row):
    try:
        v = df.loc[row, col]
        return float(v) if pd.notna(v) else None
    except (KeyError, TypeError):
        return None


def snapshot_history(ticker: str) -> list:
    """누적 스냅샷 기반 판정 이력 — 있으면 이쪽이 우선이다.

    yfinance 연간 재무제표(5년, 밸류 지표 없음)보다 훨씬 정확하다. 스냅샷에는
    **시점 선행PER·PEG 가 들어있어** 3기준을 온전히 적용할 수 있기 때문이다.
    쌓일수록 이 검증이 저절로 좋아진다 — 그게 run_snapshot.py 를 만든 이유다.
    """
    from longcore import snapshot
    recs = snapshot.load(ticker)
    out = []
    for r in recs:
        f = snapshot.as_fundamentals(r)
        res = thesis.judge_criteria(f, THESIS_CRITERIA)
        out.append({"date": r["date"], "criteria": res,
                    "failed": thesis.quarter_verdict(res, THESIS_FAIL_THRESHOLD)})
    return out


def report_snapshot_coverage(tickers) -> bool:
    """스냅샷이 검증에 쓸 만큼 쌓였는지. 충분하면 True."""
    from longcore import snapshot
    MIN_QUARTERS = 8
    print("── 누적 스냅샷 현황 ─────────────────────────────────────")
    ready = True
    for t in tickers:
        cov = snapshot.coverage(t)
        ok = cov["quarters"] >= MIN_QUARTERS
        ready = ready and ok
        status = "✓ 검증 가능" if ok else f"— {MIN_QUARTERS - cov['quarters']}분기 더 필요"
        print(f"  {t:<5} {cov['count']:>3}건 / {cov['quarters']}분기  {status}")
    if not ready:
        print("  → 아직 부족하다. 아래는 yfinance 연간 재무제표 기반 대체 검증이다")
        print("    (밸류 기준 판정 불가 → 실제 규칙보다 덜 민감 = 퇴출 횟수는 하한).\n")
    else:
        print("  → 스냅샷만으로 3기준 온전히 적용 가능. 이쪽 결과를 신뢰하라.\n")
    return ready


def main() -> int:
    cfg = BUCKETS["해외증권"]
    tickers = sorted(cfg["sleeves"]["GROWTH"])
    report_snapshot_coverage(tickers)
    print("논지 재판정 규칙 — 역사적 표적 검증")
    print(f"기준: 순이익률 {THESIS_CRITERIA['net_margin_min']:.0%}↑ + FCF 흑자 / "
          f"매출성장 {THESIS_CRITERIA['revenue_growth_min']:.0%}↑ / 밸류(과거값 부재 → UNKNOWN)")
    print(f"퇴출: {THESIS_FAIL_THRESHOLD}개 이상 미달이 {THESIS_CONSECUTIVE_OUT}기 연속\n")

    exits = {}
    for ticker in tickers:
        df = annual_fundamentals(ticker)
        print(f"── {ticker} " + "─" * 50)
        verdicts = []
        for _, r in df.iterrows():
            f = {"net_margin": r["net_margin"], "free_cashflow": r["free_cashflow"],
                 "revenue_growth": r["revenue_growth"],
                 "forward_pe": None, "peg": None}
            res = thesis.judge_criteria(f, THESIS_CRITERIA)
            failed = thesis.quarter_verdict(res, THESIS_FAIL_THRESHOLD)
            verdicts.append(failed)
            growth = (f"{r['revenue_growth']:+.1%}" if r["revenue_growth"] is not None
                      else "  n/a")
            print(f"  FY{r['period_end'].year}  매출 {r['revenue']/1e9:7.1f}B "
                  f"({growth})  순이익률 {r['net_margin']:6.1%}  "
                  f"FCF {r['free_cashflow']/1e9:7.1f}B  "
                  f"→ 수익성{_m(res['수익성'])} 성장{_m(res['성장'])} 밸류{_m(res['밸류'])}"
                  f"  {'⚠미달' if failed else '유지'}")

        streak = thesis.exit_candidates({ticker: verdicts}, THESIS_CONSECUTIVE_OUT)
        if streak:
            idx = _first_exit_index(verdicts, THESIS_CONSECUTIVE_OUT)
            when = df.iloc[idx]["available_from"]
            exits[ticker] = when
            print(f"  ⇒ 퇴출 발동: {when.date()} 이후 첫 분기 점검일")
        else:
            print("  ⇒ 퇴출 발동 없음")
        print()

    print("=" * 62)
    if not exits:
        print("판정: 이 구간에서 어떤 종목도 퇴출되지 않았다.")
        print("      규칙이 '바닥에서 팔아버리는' 사고를 내지 않았다는 뜻이다.")
        _nvda_stress(tickers)
    else:
        for ticker, when in exits.items():
            _cost_of_exit(ticker, when)
    return 0


def _first_exit_index(verdicts, consecutive):
    for i in range(consecutive - 1, len(verdicts)):
        if all(verdicts[i - consecutive + 1:i + 1]):
            return i
    return len(verdicts) - 1


def _m(v):
    return {thesis.PASS: "O", thesis.FAIL: "X", thesis.UNKNOWN: "?"}[v]


def _cost_of_exit(ticker: str, when) -> None:
    """퇴출했다면 얼마나 손해였나 — 퇴출 시점부터 지금까지 종목 vs VOO."""
    px = fetch_history([ticker, "VOO"], when.date().isoformat()).dropna()
    r_stock = px[ticker].iloc[-1] / px[ticker].iloc[0] - 1
    r_voo = px["VOO"].iloc[-1] / px["VOO"].iloc[0] - 1
    print(f"\n[{ticker}] {px.index[0].date()} 퇴출 가정 → {px.index[-1].date()}")
    print(f"  보유했다면 {r_stock:+.1%} / VOO 로 갈아탔다면 {r_voo:+.1%}")
    verdict = "규칙이 손해를 막았다" if r_voo > r_stock else "⚠ 규칙이 상승을 놓쳤다"
    print(f"  차이 {r_voo - r_stock:+.1%}p — {verdict}")


def _nvda_stress(tickers) -> None:
    """퇴출이 안 났다면, 가장 위험했던 구간에서 얼마나 아슬아슬했는지 본다."""
    if "NVDA" not in tickers:
        return
    df = annual_fundamentals("NVDA")
    worst = df.loc[df["net_margin"].idxmin()]
    print(f"\n가장 위험했던 구간 — NVDA FY{worst['period_end'].year}: "
          f"순이익률 {worst['net_margin']:.1%}, 매출성장 "
          f"{worst['revenue_growth']:+.1%}" if worst["revenue_growth"] is not None
          else "")
    res = thesis.judge_criteria(
        {"net_margin": worst["net_margin"], "free_cashflow": worst["free_cashflow"],
         "revenue_growth": worst["revenue_growth"], "forward_pe": None, "peg": None},
        THESIS_CRITERIA)
    fails = [k for k, v in res.items() if v == thesis.FAIL]
    print(f"  미달 기준: {fails or '없음'} ({len(fails)}개 / 퇴출선 {THESIS_FAIL_THRESHOLD}개)")
    if len(fails) == THESIS_FAIL_THRESHOLD - 1:
        print("  ⚠ 퇴출선 바로 아래였다 — 밸류 기준까지 판정 가능했다면 퇴출됐을 수 있다.")
        print("     실제 규칙은 이 검증보다 민감하다는 뜻이다 (한계 2 참조).")


if __name__ == "__main__":
    raise SystemExit(main())
