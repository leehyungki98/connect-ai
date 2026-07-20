"""리밸런싱 규칙 백테스트 — 확정 구성으로 분기 ±5%p 밴드 규칙 시뮬레이션.

⚠ 이 백테스트는 리밸런싱 **메커니즘 검증**용이다. 오늘 고른 종목을 과거에
적용하므로 사후 선택 편향이 있다 — 성과 수치를 "검증됨"의 근거로 쓰지 마라.
같은 데이터로 파라미터(밴드·주기) 반복 튜닝 금지 (CLAUDE.md).
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longcore import store  # noqa: E402
from longcore.config import (BACKTEST_END, BACKTEST_START, BENCHMARK,  # noqa: E402
                             BUCKETS, COMMISSION_BPS, FRACTIONAL_SHARES,
                             FX_SPREAD_BPS, MIN_COMMISSION_USD, SEC_FEE_BPS)
from longcore.data import fetch_history  # noqa: E402
from longcore.paper import execute  # noqa: E402
from longcore.portfolio import nav_usd, weights  # noqa: E402
from longcore.rebalance import check_bands, make_orders  # noqa: E402

import pandas as pd  # noqa: E402

# 백테스트 시작 자본 = 데스크 실제 자본 (2026-07-20 개정).
# $100k 같은 큰 값으로 돌리면 수수료·최소주문 영향이 과소평가된다 —
# 실제 자본이 ~$5k 인데 검증만 $100k 로 하면 그 검증은 전이되지 않는다.
DEFAULT_CAPITAL_USD = BUCKETS["해외증권"]["capital_krw"] / 1480.0   # 설립 환율 근사


def metrics(nav: pd.Series) -> dict:
    ret = nav.pct_change().dropna()
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1
    mdd = float((1 - nav / nav.cummax()).max())
    sharpe = float(ret.mean() / ret.std() * (252 ** 0.5)) if ret.std() > 0 else 0.0
    return {"CAGR": round(cagr, 4), "MDD": round(mdd, 4),
            "Sharpe": round(sharpe, 2), "total": round(nav.iloc[-1] / nav.iloc[0] - 1, 4)}


def quarterly_first_days(index, months) -> list:
    out = []
    for (y, m), grp in pd.Series(index=index, dtype=float).groupby(
            [index.year, index.month]):
        if m in months:
            out.append(grp.index[0])
    return out


def simulate(px, cfg, capital: float, *, commission_bps=COMMISSION_BPS,
             min_commission=MIN_COMMISSION_USD, sec_fee_bps=SEC_FEE_BPS,
             fractional=FRACTIONAL_SHARES, fx_spread_bps=FX_SPREAD_BPS):
    """분기 밴드 규칙 시뮬레이션. 반환: (NAV 시계열, 리밸런싱 이벤트, 총비용).

    비용 파라미터를 인자로 받는 이유: 규모·소수주 민감도 분석이 같은 루프를
    재사용해야 비교가 정직해진다 (run_cost_sensitivity.py).
    """
    tickers = sorted({t for comp in cfg["sleeves"].values() for t in comp})
    idx = px.index
    rebal_days = set(quarterly_first_days(idx, set(cfg["rebalance_months"])))

    # 환전 스프레드 — KRW→USD 최초 환전에서 1회 물린다
    capital_after_fx = capital * (1 - fx_spread_bps / 10_000.0)
    holding = {"cash_usd": capital_after_fx, "positions": {}, "initialized": False}
    first = idx[0]
    prices0 = {t: float(px.loc[first, t]) for t in tickers}
    orders = make_orders(holding, prices0, cfg, ["CASH"], fractional=fractional)
    holding, fills0 = execute(orders, prices0, holding, commission_bps,
                              min_commission, sec_fee_bps)
    holding["initialized"] = True

    total_cost = (capital - capital_after_fx) + sum(f["commission_usd"] for f in fills0)
    navs, events = [], []
    for day in idx:
        prices = {t: float(px.loc[day, t]) for t in tickers}
        if day != first and day in rebal_days:
            w = weights(holding, prices, cfg["sleeves"])
            breached = check_bands(w, cfg["targets"], cfg["band"])
            if breached:
                orders = make_orders(holding, prices, cfg, breached,
                                     fractional=fractional)
                if orders:
                    holding, fills = execute(orders, prices, holding,
                                             commission_bps, min_commission,
                                             sec_fee_bps)
                    total_cost += sum(f["commission_usd"] for f in fills)
                    events.append({"date": day.date().isoformat(),
                                   "breached": breached, "fills": len(fills)})
        navs.append(nav_usd(holding, prices))
    return pd.Series(navs, index=idx), events, total_cost


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=BACKTEST_START)
    ap.add_argument("--end", default=BACKTEST_END)
    ap.add_argument("--capital", type=float, default=DEFAULT_CAPITAL_USD,
                    help="시작 자본 USD (기본: 데스크 실제 자본)")
    args = ap.parse_args()

    cfg = BUCKETS["해외증권"]
    tickers = sorted({t for comp in cfg["sleeves"].values() for t in comp})
    px = fetch_history(tickers + [BENCHMARK], args.start, args.end).dropna()
    idx = px.index
    rebal_days = set(quarterly_first_days(idx, set(cfg["rebalance_months"])))

    nav, events, total_cost = simulate(px, cfg, args.capital)
    spy = px[BENCHMARK] / px[BENCHMARK].iloc[0] * args.capital
    print(f"총 거래비용 ${total_cost:,.2f} (시작 자본의 {total_cost/args.capital:.2%})")

    m_desk, m_spy = metrics(nav), metrics(spy)
    print(f"구간 {idx[0].date()} ~ {idx[-1].date()} · 시작 ${args.capital:,.0f} · "
          f"수수료 {COMMISSION_BPS}bps")
    print(f"리밸런싱 실행 {len(events)}회 / 분기 점검 {len(rebal_days)}회 "
          f"(이탈 없으면 주문 0건)")
    for e in events:
        print(f"  {e['date']}  이탈 {e['breached']}  체결 {e['fills']}건")
    print(f"{'':<8}{'CAGR':>8}{'MDD':>8}{'Sharpe':>8}{'누적':>10}")
    print(f"{'데스크':<8}{m_desk['CAGR']:>8.2%}{m_desk['MDD']:>8.2%}"
          f"{m_desk['Sharpe']:>8.2f}{m_desk['total']:>10.2%}")
    print(f"{'SPY TR':<8}{m_spy['CAGR']:>8.2%}{m_spy['MDD']:>8.2%}"
          f"{m_spy['Sharpe']:>8.2f}{m_spy['total']:>10.2%}")
    print("⚠ 사후 선택 편향: 오늘 고른 종목을 과거에 적용한 수치 — 참고만 할 것.")

    out = store.STATE_DIR / f"backtest_{date.today().strftime('%Y%m%d')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "start": str(idx[0].date()), "end": str(idx[-1].date()),
        "desk": m_desk, "spy": m_spy, "rebalances": events,
        "commission_bps": COMMISSION_BPS,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: state/{out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
