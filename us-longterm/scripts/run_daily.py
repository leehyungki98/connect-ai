"""일일 스냅샷 — NAV·비중·환노출 기록, 밴드 이탈 플래그. 주문 절대 없음."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longcore import store  # noqa: E402
from longcore.config import BUCKETS, FX_TICKER, INDEX_LOOKTHROUGH  # noqa: E402
from longcore.data import fetch_history  # noqa: E402
from longcore.portfolio import effective_weights, fx_exposure, weights  # noqa: E402
from longcore.rebalance import check_bands, deviations  # noqa: E402

import pandas as pd  # noqa: E402


def main() -> int:
    holdings = store.load_holdings()
    exit_code = 0
    for bucket, cfg in BUCKETS.items():
        holding = holdings.get(bucket)
        if not holding or not holding.get("initialized"):
            print(f"[{bucket}] 미초기화 — `python scripts/run_rebalance.py --init` 먼저.")
            exit_code = 1
            continue

        tickers = sorted({t for comp in cfg["sleeves"].values() for t in comp})
        start = (pd.Timestamp.today() - pd.Timedelta(days=30)).date().isoformat()
        hist = fetch_history(tickers + [FX_TICKER], start).ffill()
        asof = hist.index[-1].date()
        prices = {t: float(hist[t].iloc[-1]) for t in tickers}
        usdkrw = float(hist[FX_TICKER].iloc[-1])

        w = weights(holding, prices, cfg["sleeves"])
        fx = fx_exposure(holding, prices, usdkrw)
        dev = deviations(w, cfg["targets"])
        breached = check_bands(w, cfg["targets"], cfg["band"])

        eff = effective_weights(holding["positions"], holding["cash_usd"],
                                prices, INDEX_LOOKTHROUGH)
        growth = sorted(cfg["sleeves"].get("GROWTH", {}))
        cap = cfg["max_single_stock"]

        store.append_jsonl("nav_log.jsonl", {
            "date": asof.isoformat(), "bucket": bucket,
            "nav_usd": round(fx["nav_usd"], 2), "nav_krw": round(fx["nav_krw"], 0),
            "usdkrw": round(usdkrw, 2),
            "weights": {k: round(v, 4) for k, v in w.items()},
            "deviations": {k: round(v, 4) for k, v in dev.items()},
            "effective_weights": {t: round(eff.get(t, 0.0), 4) for t in growth},
            "band_breached": breached,
        })
        store.append_jsonl("fx_log.jsonl", {
            "date": asof.isoformat(), "usdkrw": round(usdkrw, 4),
        })

        print(f"[{bucket}] {asof} NAV ${fx['nav_usd']:,.2f} "
              f"(₩{fx['nav_krw']:,.0f}, USDKRW {usdkrw:,.2f})")
        for s in cfg["targets"]:
            print(f"  {s:<6} {w.get(s, 0):7.2%}  (목표 {cfg['targets'][s]:.0%}, "
                  f"이탈 {dev[s]:+.2%})")
        print(f"  실효 비중 (ETF 내부 중복 포함, 상한 {cap:.0%}):")
        for t in growth:
            direct = eff.get(t, 0.0) - sum(
                (holding["positions"].get(etf, {}).get("shares", 0.0)
                 * prices.get(etf, 0.0) / fx["nav_usd"]) * comp.get(t, 0.0)
                for etf, comp in INDEX_LOOKTHROUGH.items())
            room = cap - eff.get(t, 0.0)
            flag = " ⚠ 상한 초과" if room < 0 else ""
            print(f"    {t:<5} {eff.get(t, 0.0):6.2%}  (직접 {direct:.2%} + "
                  f"ETF 경유 {eff.get(t, 0.0) - direct:.2%}, 여유 {room:+.2%}){flag}")

        if breached:
            print(f"  ⚠ 밴드 이탈: {breached} — 분기 점검일에 리밸런싱 대상 "
                  f"(오늘은 기록만, 주문 없음)")
        else:
            print("  밴드 내 — 조치 없음")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
