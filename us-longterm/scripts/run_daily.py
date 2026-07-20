"""일일 스냅샷 — NAV·비중·환노출 기록, 밴드 이탈 플래그. 주문 절대 없음."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longcore import positions, store  # noqa: E402
from longcore.clock import is_partial_bar, us_eastern_now  # noqa: E402
from longcore.config import BUCKETS, FX_TICKER, INDEX_LOOKTHROUGH  # noqa: E402
from longcore.data import fetch_history  # noqa: E402
from longcore.portfolio import effective_weights, fx_exposure, weights  # noqa: E402
from longcore.rebalance import check_bands, deviations  # noqa: E402

import pandas as pd  # noqa: E402


def _write_positions_view(bucket, holding, prices, usdkrw, asof):
    """브로커 앱 스타일 현재 평가 — 대시보드 카드가 그대로 렌더 (표시 전용)."""
    cb = positions.cost_basis(store.LEDGER_DIR / "rebalances")
    rows, tot_val, tot_cost = [], 0.0, 0.0
    for sym, pos in holding["positions"].items():
        shares = pos["shares"]
        cost = cb.get(sym, {}).get("cost_usd", 0.0)
        ev = positions.evaluate(shares, prices[sym], cost, usdkrw)
        ev["ticker"] = sym
        rows.append(ev)
        tot_val += ev["value_usd"]
        tot_cost += ev["cost_usd"]
    rows.sort(key=lambda r: -r["value_usd"])
    cash = holding["cash_usd"]
    view = {
        "asof": asof.isoformat(), "usdkrw": round(usdkrw, 2), "cash_usd": round(cash, 2),
        "cash_krw": round(cash * usdkrw), "positions": rows,
        "total": {
            "value_usd": round(tot_val + cash, 2), "cost_usd": round(tot_cost, 2),
            "pnl_usd": round(tot_val - tot_cost, 2),
            "ret_pct": round((tot_val - tot_cost) / tot_cost * 100, 2) if tot_cost else 0.0,
            "value_krw": round((tot_val + cash) * usdkrw),
        },
    }
    (store.STATE_DIR / "positions_view.json").write_text(
        json.dumps(view, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_positions_history(bucket, holding, hist, usdkrw, inception_date):
    """종목별 일별 평가금액 곡선 (그래프용). 설립일 이후 구간을 현재 수량 × 과거
    종가로 계산. ⚠ 리밸런싱 전까지는 정확(수량 불변) — 첫 리밸런싱(10/1) 후엔
    수량 타임라인 재구성이 필요하다. 그 전까진 이 근사가 실제와 일치한다."""
    idx = [d for d in hist.index if d.date().isoformat() >= inception_date]
    if not idx:
        return
    dates = [d.date().isoformat() for d in idx]
    series, total = {}, [0.0] * len(idx)
    cash = holding["cash_usd"]
    for sym, pos in holding["positions"].items():
        sh = pos["shares"]
        vals = [round(sh * float(hist.loc[d, sym]), 2) for d in idx]
        series[sym] = vals
        total = [t + v for t, v in zip(total, vals)]
    total = [round(t + cash, 2) for t in total]
    out = {"dates": dates, "series": series, "total": total,
           "usdkrw": round(usdkrw, 2)}
    (store.STATE_DIR / "positions_history.json").write_text(
        json.dumps(out, ensure_ascii=False), encoding="utf-8")


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

        # 데이터 품질 가드 (기록은 그대로 진행 — 출력만): 미국장이 열려 있는
        # 동안 돌리면 오늘 일봉이 미완성(장중 값)일 수 있다. 마감 후 다시 돌리면
        # ledger 미러가 (date,bucket) 멱등으로 이 값을 교정한다.
        if is_partial_bar(asof, us_eastern_now()):
            print(f"[{bucket}] ⚠ {asof} 는 아직 장중일 수 있다 — 이 종가는 미완성일 "
                  f"가능성. 미국장 마감(한국시간 새벽 5~6시) 후 다시 돌리면 교정됨.")

        w = weights(holding, prices, cfg["sleeves"])
        fx = fx_exposure(holding, prices, usdkrw)
        dev = deviations(w, cfg["targets"])
        breached = check_bands(w, cfg["targets"], cfg["band"])

        eff = effective_weights(holding["positions"], holding["cash_usd"],
                                prices, INDEX_LOOKTHROUGH)
        growth = sorted(cfg["sleeves"].get("GROWTH", {}))
        cap = cfg["max_single_stock"]

        nav_record = {
            "date": asof.isoformat(), "bucket": bucket,
            "nav_usd": round(fx["nav_usd"], 2), "nav_krw": round(fx["nav_krw"], 0),
            "usdkrw": round(usdkrw, 2),
            "weights": {k: round(v, 4) for k, v in w.items()},
            "deviations": {k: round(v, 4) for k, v in dev.items()},
            "effective_weights": {t: round(eff.get(t, 0.0), 4) for t in growth},
            "band_breached": breached,
        }
        store.append_jsonl("nav_log.jsonl", nav_record)   # state/ 원본 (append)
        store.mirror_nav_record(nav_record)               # ledger/ 미러 (git 추적)
        store.append_jsonl("fx_log.jsonl", {
            "date": asof.isoformat(), "usdkrw": round(usdkrw, 4),
        })

        # 브로커 앱 스타일 표시 데이터 (대시보드 카드용, 표시 전용)
        _write_positions_view(bucket, holding, prices, usdkrw, asof)
        _write_positions_history(bucket, holding, hist, usdkrw,
                                 holding["inception"]["date"])

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
