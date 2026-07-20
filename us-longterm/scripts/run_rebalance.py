"""분기 점검일 실행 — 게이트(분기일·밴드 이탈·이탈 슬리브 한정) 통과 시에만 페이퍼 주문.

실행 순서 (2026-07-20 개정 — 물타기 방지의 핵심):
  ① 논지 재판정 결과 반영 (퇴출 종목을 성장주 슬리브에서 제거, 목표를 VOO 가 흡수)
  ② 밴드 판정 (살아남은 구성으로)
  ③ 주문 생성 → 게이트 → 체결
순서가 뒤집히면 논지가 깨진 종목을 밴드가 기계적으로 추가 매수한다.
**물타기는 논지가 살아있는 종목에만 허용된다.**

--init : 최초 배분 (보유가 비어 있을 때만 1회, 분기일 게이트 면제 — 나머지 전부 적용)
--dry-run : 주문 목록만 출력, 체결·기록 없음 (기본 권장)
--confirm-exits A,B : 퇴출 종목 명시 승인. run_review 가 낸 후보와 정확히 일치해야
  진행된다 (자동 퇴출 없음 — 사용자가 티커를 직접 적어야 한다)
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longcore import store, thesis  # noqa: E402
from longcore.config import (BUCKETS, COMMISSION_BPS, FRACTIONAL_SHARES,  # noqa: E402
                             FREE_BELOW_USD, FX_SPREAD_BPS, FX_TICKER,
                             MIN_COMMISSION_USD, SEC_FEE_MIN_USD, SEC_FEE_BPS,
                             THESIS_CONSECUTIVE_OUT, THESIS_EXIT_TARGET)
from longcore.data import fetch_history  # noqa: E402
from longcore.gates import paper_only, rebalance_gate  # noqa: E402
from longcore.paper import execute, record_rebalance  # noqa: E402
from longcore.portfolio import nav_usd, weights  # noqa: E402
from longcore.rebalance import check_bands, make_orders  # noqa: E402
from longcore.safety import guard  # noqa: E402
from longcore.types import Order  # noqa: E402

import pandas as pd  # noqa: E402


def fail(msgs) -> int:
    for m in msgs:
        print(f"  거부: {m}")
    return 1


def exit_candidates_from_state(cfg: dict) -> list:
    """run_review 가 적재한 판정 이력에서 퇴출 후보를 재계산한다.

    이력 파일이 없으면 후보 없음 — 재판정을 한 번도 안 돌린 상태다.
    여기서 다시 계산하는 이유: run_review 의 출력을 신뢰하지 않고 같은 규칙을
    같은 데이터에 다시 적용해야 승인 대상이 조작될 여지가 없다.
    """
    p = store.STATE_DIR / "thesis_history.json"
    if not p.exists():
        return []
    history = json.loads(p.read_text(encoding="utf-8"))
    growth = set(cfg["sleeves"].get("GROWTH", {}))
    streaks = {t: [r["failed"] for r in recs]
               for t, recs in history.items() if t in growth}
    return thesis.exit_candidates(streaks, THESIS_CONSECUTIVE_OUT)


def exit_orders(holding: dict, prices: dict, exits) -> list:
    """퇴출 종목 전량 매도. 대금은 같은 회차의 VOO 매수로 흡수된다
    (make_orders 가 목표 흡수분을 이미 반영한다)."""
    out = []
    for ticker in exits:
        shares = holding.get("positions", {}).get(ticker, {}).get("shares", 0.0)
        if shares > 0:
            out.append(Order(ticker=ticker, side="SELL", qty=shares,
                             sleeve="GROWTH", ref_price=prices[ticker]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true", help="최초 배분 (보유 0일 때만)")
    ap.add_argument("--dry-run", action="store_true", help="체결 없이 주문만 출력")
    ap.add_argument("--confirm-exits", default="",
                    help="퇴출 승인 티커 (쉼표 구분) — run_review 후보와 일치해야 함")
    args = ap.parse_args()
    confirmed = {t.strip().upper() for t in args.confirm_exits.split(",") if t.strip()}

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
        candidates = []

        if args.init:
            g = rebalance_gate.check_init(holding)
            if not g.ok:
                rc = fail(g.reasons)
                continue
            # 환전 스프레드 — KRW→USD 환전에서 1회 물린다 (실계좌의 실제 비용)
            capital_usd = (cfg["capital_krw"] / usdkrw) * (1 - FX_SPREAD_BPS / 10_000.0)
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

            # ① 논지 재판정 — 밴드보다 먼저. 퇴출 종목은 밴드 리밸런싱 대상에서
            #    빠지므로 물타기가 일어나지 않는다.
            candidates = exit_candidates_from_state(cfg)
            if candidates and confirmed != set(candidates):
                rc = fail([
                    f"퇴출 후보 {candidates} 가 미승인 상태 — 자동 퇴출은 없다.",
                    f"승인하려면: --confirm-exits {','.join(candidates)}",
                    "먼저 `python scripts/run_review.py` 로 판정 근거를 확인할 것.",
                ])
                continue
            if confirmed and confirmed != set(candidates):
                rc = fail([f"승인 목록 {sorted(confirmed)} 이 후보 {candidates} 와 불일치"])
                continue

            if candidates:
                cfg = dict(cfg)
                cfg["sleeves"] = thesis.apply_exits(cfg["sleeves"], candidates,
                                                    THESIS_EXIT_TARGET)
                cfg["targets"] = thesis.absorb_targets(cfg["targets"],
                                                       cfg["sleeves"])
                print(f"  논지 퇴출 반영: {candidates} → {THESIS_EXIT_TARGET} "
                      f"(손절 아님 — 지수로 강등)")

            # ② 밴드 판정 — 살아남은 구성 기준
            w = weights(holding, prices, cfg["sleeves"])
            breached = check_bands(w, cfg["targets"], cfg["band"])
            if candidates and "GROWTH" not in breached:
                breached = breached + ["GROWTH"]   # 퇴출 처분은 밴드와 무관하게 실행
            reason = (f"분기 점검 — 이탈 슬리브 {breached}"
                      + (f", 논지 퇴출 {candidates}" if candidates else ""))
            tag = "quarterly"

        # ③ 주문 생성
        orders = make_orders(holding, prices, cfg, breached,
                             fractional=FRACTIONAL_SHARES)
        if not args.init and candidates:
            orders = orders + exit_orders(holding, prices, candidates)

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

        new_holding, fills = execute(orders, prices, holding, COMMISSION_BPS,
                                     MIN_COMMISSION_USD, SEC_FEE_BPS,
                                     FREE_BELOW_USD, SEC_FEE_MIN_USD)
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
