"""정세 자동 채점 — pending 뉴스를 LLM 으로 impact/축/근거로 수치화해 ledger 에 적재.

06:00 수집(collect_wsj) 직후 도는 단계. run_intel_review.py 의 사람 입력을 대체한다
(사람 검수는 선택적 덮어쓰기로 남는다).

로직:
  1. state/intel_staging/{분기}.jsonl 에서 pending 로드
  2. 우선순위(held > watchlist > theme) 로 정렬 — 매도/VOO 판단이 보유에 달렸다
  3. 배치로 묶어 intel_llm.score_batch → intel.record() (검증 통과분만)
  4. 처리분 status="recorded"/"skipped", jsonl 재저장 (멱등)
  5. 보유 종목 최근 급락(recent_drop) 경보 출력

--limit N : 이번 실행에서 처리할 최대 건수 (백필을 여러 번에 나눠 돌릴 때).
--tier held,watchlist : 이 계층만 채점 (기본: 전부).
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longcore import intel, intel_llm, intel_sources  # noqa: E402
from longcore.config import BUCKETS  # noqa: E402
from longcore.intel import quarter_of  # noqa: E402
from longcore.store import STATE_DIR  # noqa: E402

STAGING_DIR = STATE_DIR / "intel_staging"
_TIER_RANK = {"held": 0, "watchlist": 1, "theme": 2, "candidate": 3, "macro": 4}


def _held_tickers() -> set:
    out = set()
    for cfg in BUCKETS.values():
        out |= set(cfg.get("sleeves", {}).get("GROWTH", {}))
    return out


def _load(quarter: str) -> list:
    p = STAGING_DIR / f"{quarter}.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _save(quarter: str, rows: list) -> None:
    p = STAGING_DIR / f"{quarter}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _top_tier(rec: dict) -> str:
    tiers = set((rec.get("tiers") or {}).values())
    for t in _TIER_RANK:
        if t in tiers:
            return t
    return "macro"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="최대 처리 건수 (0=전부)")
    ap.add_argument("--tier", type=str, default="", help="채점할 계층 쉼표구분 (기본 전부)")
    args = ap.parse_args()

    today = date.today()
    quarter = quarter_of(today)
    rows = _load(quarter)
    pending_idx = [i for i, r in enumerate(rows) if r.get("status") == "pending"]
    if not pending_idx:
        print("채점할 pending 뉴스 없음.")
        return 0

    want_tiers = set(args.tier.split(",")) if args.tier else None
    # 우선순위 정렬 — 보유부터
    pending_idx.sort(key=lambda i: _TIER_RANK.get(_top_tier(rows[i]), 9))
    if want_tiers:
        pending_idx = [i for i in pending_idx if _top_tier(rows[i]) in want_tiers]
    if args.limit > 0:
        pending_idx = pending_idx[:args.limit]

    print(f"pending {len(pending_idx)}건 채점 (분기 {quarter}) — 배치 {intel_llm.BATCH_SIZE}건씩")

    recorded = skipped = 0
    held = _held_tickers()
    touched_held = set()
    for start in range(0, len(pending_idx), intel_llm.BATCH_SIZE):
        chunk = pending_idx[start:start + intel_llm.BATCH_SIZE]
        articles = [{
            "idx": i, "title": rows[i]["title"],
            "summary": rows[i].get("summary", ""),
            "tickers": [t for t in rows[i].get("matched", []) if t != "MACRO"],
        } for i in chunk if [t for t in rows[i].get("matched", []) if t != "MACRO"]]
        scored = intel_llm.score_batch(articles)
        by_idx = {}
        for e in scored:
            by_idx.setdefault(e["idx"], []).append(e)

        for i in chunk:
            entries = by_idx.get(i, [])
            if not entries:
                rows[i]["status"] = "skipped"    # 관련 티커 없음 or 채점 실패
                skipped += 1
                continue
            for e in entries:
                intel.record(
                    ticker=e["ticker"], event_date=today,
                    source=rows[i].get("section", "news"),
                    event=rows[i]["title"][:200], impact=e["impact"],
                    thesis_axis=e["thesis_axis"], basis=e["basis"])
                if e["ticker"] in held:
                    touched_held.add(e["ticker"])
            rows[i]["status"] = "recorded"
            recorded += 1
        print(f"  ...{start + len(chunk)}/{len(pending_idx)}")

    _save(quarter, rows)
    print(f"\n완료 — 기록 {recorded} · 스킵 {skipped}")

    # 보유 종목 최근 급락 경보
    alerts = []
    for tk in sorted(touched_held):
        summary = intel.recent_drop(intel.load_quarter(tk, quarter), today)
        if summary:
            alerts.append((tk, summary))
    if alerts:
        print("\n🚨 보유 종목 분위기 급락 경보")
        for tk, s in alerts:
            print(f"  {tk} — 최근 {s['window_days']}일 정세점수 {s['score']} "
                  f"({s['count']}건). 매도/VOO 검토 대상.")
            for w in s["worst"]:
                print(f"     {w['impact']:+d} [{w['thesis_axis']}] {w['basis'][:50]}")
    else:
        print("\n보유 종목 급락 경보 없음.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
