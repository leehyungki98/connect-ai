"""성과 리포트 — 벤치마크(SPY 총수익) 대비. USD 기준 판정, KRW 환산 병기 +
환율 기여 분해. 동일 시점(설립일) 투입 가정."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longcore import store  # noqa: E402
from longcore.config import BENCHMARK, BUCKETS, FX_TICKER  # noqa: E402
from longcore.data import fetch_history  # noqa: E402
from longcore.portfolio import nav_usd  # noqa: E402

import pandas as pd  # noqa: E402


def main() -> int:
    holdings = store.load_holdings()
    rc = 0
    for bucket, cfg in BUCKETS.items():
        holding = holdings.get(bucket)
        if not holding or not holding.get("initialized"):
            print(f"[{bucket}] 미초기화 — 리포트 없음")
            rc = 1
            continue
        inc = holding["inception"]
        tickers = sorted({t for comp in cfg["sleeves"].values() for t in comp})
        # 설립 당일 실행 대비: 설립일 7일 전부터 조회 후 설립일 이후 첫 값을 기준점으로
        start = (pd.Timestamp(inc["date"]) - pd.Timedelta(days=7)).date().isoformat()
        hist = fetch_history(tickers + [BENCHMARK, FX_TICKER], start).ffill()
        asof = hist.index[-1].date()
        prices = {t: float(hist[t].iloc[-1]) for t in tickers}
        usdkrw = float(hist[FX_TICKER].iloc[-1])
        spy = hist[BENCHMARK].dropna()
        since = spy[spy.index >= inc["date"]]
        spy0 = float(since.iloc[0]) if not since.empty else float(spy.iloc[-1])
        spy1 = float(spy.iloc[-1])

        nav = nav_usd(holding, prices)
        r_usd = nav / inc["capital_usd"] - 1
        r_spy = spy1 / spy0 - 1
        nav_krw = nav * usdkrw
        r_krw = nav_krw / inc["capital_krw"] - 1
        r_fx = usdkrw / inc["usdkrw"] - 1

        print(f"[{bucket}] {inc['date']} ~ {asof}")
        print(f"  NAV        ${nav:,.2f}  (₩{nav_krw:,.0f})")
        print(f"  데스크 USD  {r_usd:+.2%}   ← 판정 기준")
        print(f"  SPY  TR    {r_spy:+.2%}   → 초과수익 {r_usd - r_spy:+.2%}")
        print(f"  데스크 KRW  {r_krw:+.2%}   (환율 {inc['usdkrw']:,.2f} → {usdkrw:,.2f})")
        print(f"  분해: USD수익 {r_usd:+.2%} × 환율 {r_fx:+.2%} "
              f"(교차항 포함 합 = KRW {r_krw:+.2%})")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
