"""미장팀 사후분석(노유진) — 매일 07:10, 스냅샷 뒤에 돈다.

성과(SPY 대비·환율 분해) + 보유 종목 정세 + 비중 이탈을 한 데 모아 쉬운 말로 낸다.
결과를 ledger/reviews/{날짜}_daily.md 에 저장(재생성 안 되는 학습 자산이라 git 추적)하고
표준출력에도 찍는다. 매매·주문 없음 — 순수 관찰.
"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longcore import intel, review, store  # noqa: E402
from longcore.config import (BENCHMARK, BUCKETS, FX_TICKER,  # noqa: E402
                             INDEX_LOOKTHROUGH)
from longcore.data import fetch_history  # noqa: E402
from longcore.intel import quarter_of  # noqa: E402
from longcore.portfolio import effective_weights, nav_usd, weights  # noqa: E402
from longcore.rebalance import check_bands  # noqa: E402
from longcore.schedule import days_until_check  # noqa: E402

import pandas as pd  # noqa: E402


def _held_growth(cfg: dict) -> list:
    return sorted(cfg.get("sleeves", {}).get("GROWTH", {}))


def main() -> int:
    today = date.today()
    quarter = quarter_of(today)
    holdings = store.load_holdings()
    outputs = []

    for bucket, cfg in BUCKETS.items():
        holding = holdings.get(bucket)
        if not holding or not holding.get("initialized"):
            print(f"[{bucket}] 미초기화 — 사후분석 없음")
            continue

        inc = holding["inception"]
        tickers = sorted({t for comp in cfg["sleeves"].values() for t in comp})
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
        perf = review.perf_summary(
            nav, inc["capital_usd"], spy0, spy1,
            nav * usdkrw, inc["capital_krw"], inc["usdkrw"], usdkrw)

        # 보유 성장주 정세 (VOO 는 지수라 종목 정세 없음)
        moods = {tk: review.ticker_mood(intel.load_quarter(tk, quarter), today)
                 for tk in _held_growth(cfg)}

        # 비중 이탈
        w = weights(holding, prices, cfg["sleeves"])
        breached = check_bands(w, cfg["targets"], cfg["band"])
        # 실효 비중 상한 초과도 짚을 거리 (룩스루) — 표시용
        _ = effective_weights(holding["positions"], holding["cash_usd"],
                              prices, INDEX_LOOKTHROUGH)

        text = review.narrate(bucket, asof.isoformat(), perf, moods,
                              breached, days_until_check(today))
        print(text)
        print()
        outputs.append(text)

    if outputs:
        out = ROOT / "ledger" / "reviews" / f"{today.isoformat()}_daily.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n\n---\n\n".join(outputs) + "\n", encoding="utf-8")
        print(f"[저장] {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
