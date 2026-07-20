"""분기 점검일 실행 — 게이트(분기일·밴드 이탈·이탈 슬리브 한정) 통과 시에만 페이퍼 주문.

--init : 최초 배분 (보유가 비어 있을 때만 1회, 분기일 게이트 면제 — 나머지 전부 적용)
--dry-run : 주문 목록만 출력, 체결·기록 없음 (기본 권장)
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longcore import store  # noqa: E402
from longcore.config import BUCKETS, COMMISSION_BPS, FX_TICKER  # noqa: E402
from longcore.data import fetch_history  # noqa: E402
from longcore.gates import paper_only, rebalance_gate  # noqa: E402
from longcore.paper import execute, record_rebalance  # noqa: E402
from longcore.portfolio import nav_usd, weights  # noqa: E402
from longcore.rebalance import check_bands, make_orders  # noqa: E402
from longcore.safety import guard  # noqa: E402

import pandas as pd  # noqa: E402


def fail(msgs) -> int:
    for m in msgs:
        print(f"  거부: {m}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true", help="최초 배분 (보유 0일 때만)")
    ap.add_argument("--dry-run", action="store_true", help="체결 없이 주문만 출력")
    args = ap.parse_args()

    holdings = store.load_holdings()
    rc = 0
    for bucket, cfg in BUCKETS.items():
        print(f"[{bucket}] {'최초 배분' if args.init else '분기 점검'}"
              f"{' (dry-run)' if args.dry_run else ''}")
        tickers = sorted({t for comp in cfg["sleeves"].values() for t in comp})
        start = (pd.Timestamp.today() - pd.Timedelta(days=45)).date().isoformat()
        hist = fetch_history(tickers + [FX_TICKER], start).ffill()
        asof = hist.index[-1].date()
        prices = {t: float(hist[t].iloc[-1]) for t in tickers}
        usdkrw = float(hist[FX_TICKER].iloc[-1])
        trading_days = [d.date() for d in hist[tickers].dropna().index]

        holding = holdings.get(bucket, {"cash_usd": 0.0, "positions": {},
                                        "initialized": False})

        if args.init:
            g = rebalance_gate.check_init(holding)
            if not g.ok:
                rc = fail(g.reasons)
                continue
            capital_usd = cfg["capital_krw"] / usdkrw
            holding = {
                "cash_usd": capital_usd, "positions": {}, "initialized": False,
                "inception": {
                    "date": asof.isoformat(), "usdkrw": round(usdkrw, 4),
                    "capital_krw": cfg["capital_krw"],
                    "capital_usd": round(capital_usd, 2),
                },
            }
            breached = ["CASH"]   # 전 슬리브 허가 (최초 배분 = 전면 배분)
            reason = "최초 배분 (데스크 설립)"
            tag = "init"
        else:
            if not holding.get("initialized"):
                rc = fail(["미초기화 버킷 — --init 먼저"])
                continue
            w = weights(holding, prices, cfg["sleeves"])
            breached = check_bands(w, cfg["targets"], cfg["band"])
            reason = f"분기 점검 — 이탈 슬리브 {breached}"
            tag = "quarterly"

        orders = make_orders(holding, prices, cfg, breached)

        if not args.init:
            g = rebalance_gate.check(asof, trading_days, breached, orders, cfg)
            if not g.ok:
                rc = fail(g.reasons)
                continue

        for gate_name, g in (("paper_only", paper_only.check(orders)),
                             ("guard", guard.check(orders, holding, prices, cfg,
                                                   COMMISSION_BPS))):
            if not g.ok:
                print(f"  [{gate_name}]")
                rc = fail(g.reasons)
                orders = None
                break
        if orders is None:
            continue

        w_before = weights(holding, prices, cfg["sleeves"])
        print(f"  기준일 {asof} · NAV ${nav_usd(holding, prices):,.2f} · 주문 {len(orders)}건")
        for o in orders:
            print(f"    {o.side:<4} {o.ticker:<5} {o.qty:10.4f}주 "
                  f"@ ${o.ref_price:,.2f} ({o.sleeve})")
        if args.dry_run:
            print("  dry-run — 체결·기록 없음")
            continue

        new_holding, fills = execute(orders, prices, holding, COMMISSION_BPS)
        new_holding["initialized"] = True
        w_after = weights(new_holding, prices, cfg["sleeves"])
        path = record_rebalance(
            store.LEDGER_DIR / "rebalances", asof.isoformat(), tag, reason,
            w_before, w_after, nav_usd(holding, prices),
            nav_usd(new_holding, prices), fills)
        holdings[bucket] = new_holding
        store.save_holdings(holdings)
        store.append_jsonl("rebalance_log.jsonl", {
            "ts": datetime.now(timezone.utc).isoformat(), "bucket": bucket,
            "tag": tag, "fills": len(fills), "ledger": path.name,
        })
        print(f"  체결 완료 — ledger/rebalances/{path.name}")
        print(f"  체결 후 비중: " + ", ".join(
            f"{s} {w_after[s]:.2%}" for s in cfg["targets"]))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
