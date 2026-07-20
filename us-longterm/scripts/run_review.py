"""분기 리뷰 — 논지 재판정 + 정세 집계. **제안만 낸다. 매매하지 않는다.**

실행하면: 성장주 3기준 재판정 → 이력에 적재 → 퇴출 후보 산출 → 정세 요약 병기.
퇴출 후보가 나오면 사용자 승인 뒤 run_rebalance --confirm-exits 로 넘어간다.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longcore import intel, store, thesis  # noqa: E402
from longcore.config import (BUCKETS, THESIS_CONSECUTIVE_OUT,  # noqa: E402
                             THESIS_CRITERIA, THESIS_EXIT_TARGET,
                             THESIS_FAIL_THRESHOLD)
from longcore.data.fundamentals import fetch_many  # noqa: E402

HISTORY_FILE = "thesis_history.json"

MARK = {thesis.PASS: "O", thesis.FAIL: "X", thesis.UNKNOWN: "?"}


def load_history() -> dict:
    p = store.STATE_DIR / HISTORY_FILE
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_history(h: dict) -> None:
    store.STATE_DIR.mkdir(parents=True, exist_ok=True)
    (store.STATE_DIR / HISTORY_FILE).write_text(
        json.dumps(h, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="이력에 적재하지 않고 판정만 출력")
    args = ap.parse_args()

    today = date.today()
    quarter = intel.quarter_of(today)
    cfg = BUCKETS["해외증권"]
    growth = sorted(cfg["sleeves"]["GROWTH"])
    if not growth:
        print("성장주 슬리브가 비어 있음 — 재판정 대상 없음")
        return 0

    print(f"분기 리뷰 {quarter} · 기준일 {today} · 대상 {growth}")
    print("기준: 순이익률 15%↑+FCF흑자 / 매출 +15%↑ / 선행PER 30↓ 또는 PEG 1.5↓\n")

    fundamentals = fetch_many(growth)
    history = load_history()

    for ticker in growth:
        f = fundamentals[ticker]
        result = thesis.judge_criteria(f, THESIS_CRITERIA)
        failed = thesis.quarter_verdict(result, THESIS_FAIL_THRESHOLD)

        marks = " ".join(f"{k}{MARK[v]}" for k, v in result.items())
        print(f"[{ticker}] {marks}  →  {'미달' if failed else '유지'}")
        print(f"   순이익률 {_pct(f['net_margin'])} · FCF {_num(f['free_cashflow'])} · "
              f"매출성장 {_pct(f['revenue_growth'])} · "
              f"선행PER {_num(f['forward_pe'])} · PEG {_num(f['peg'])}")

        entries = intel.load_quarter(ticker, quarter)
        if entries:
            s = intel.summarize(entries)
            print(f"   정세: {s['count']}건, 점수 {s['score']:+d}, 축별 {s['by_axis']}")
            for w in s["worst"]:
                print(f"     - [{w['impact']:+d}] {w['event']} ({w['basis']})")
        else:
            print("   정세: 기록 없음 (강해원 미가동 분기)")

        if not args.dry_run:
            rec = history.setdefault(ticker, [])
            if rec and rec[-1]["quarter"] == quarter:
                rec[-1] = {"quarter": quarter, "failed": failed, "criteria": result}
            else:
                rec.append({"quarter": quarter, "failed": failed, "criteria": result})
        print()

    if not args.dry_run:
        save_history(history)

    streaks = {t: [r["failed"] for r in history.get(t, [])] for t in growth}
    exits = thesis.exit_candidates(streaks, THESIS_CONSECUTIVE_OUT)

    print("─" * 60)
    for t in growth:
        seq = "".join("X" if v else "O" for v in streaks[t][-4:]) or "-"
        print(f"  {t:<5} 최근 판정 {seq}  (X=미달)")
    if exits:
        print(f"\n⚠ 퇴출 후보: {exits} — {THESIS_CONSECUTIVE_OUT}분기 연속 미달")
        print(f"   승인 시 해당 종목 전량 → {THESIS_EXIT_TARGET} 편입 (현금화 아님).")
        print(f"   실행: python scripts/run_rebalance.py --dry-run "
              f"--confirm-exits {','.join(exits)}")
        print("   ※ 손절이 아니다 — 값이 아니라 논지가 깨져서 지수로 강등하는 것이다.")
    else:
        print("\n퇴출 후보 없음 — 성장주 슬리브 유지")
    print("\n판정 근거는 ledger/reviews/ 에 사람이 직접 기록한다 (재생성 불가 자산).")
    return 0


def _pct(v):
    return f"{v:.1%}" if isinstance(v, float) else "결측"


def _num(v):
    if not isinstance(v, float):
        return "결측"
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
