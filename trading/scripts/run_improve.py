"""분석·개선 사이클: 운용 로그 요약 → GPT 개선안 → 코다리(Opus) diff → 변경 제안 큐.

사용법: python scripts/run_improve.py [--proposer codex|claude] [--coder opus|claude]
비용: 선정자 1콜 + 개선안 개수만큼 코다리 콜 (최대 3).
제출된 카드는 사용자가 심사한다 — 자동 승인 없음.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows 콘솔(cp949)에서 LLM 유래 문자 출력 크래시 방지
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

from autotrader.config import STATE_DIR
from autotrader.improve import run_improve_cycle
from autotrader.proposals import ProposalQueue


def build_perf_summary(state_dir: Path, max_lines: int = 14) -> str:
    parts = []
    eq = state_dir / "equity_log.jsonl"
    if eq.exists():
        lines = eq.read_text(encoding="utf-8").splitlines()[-max_lines:]
        parts.append("[평가액 로그 (최근)]\n" + "\n".join(lines))
    pm = state_dir / "premarket_log.jsonl"
    if pm.exists():
        lines = pm.read_text(encoding="utf-8").splitlines()[-3:]
        parts.append("[장 시작 전 실행 리포트 (최근 3회)]\n" + "\n".join(lines))
    reviews = sorted((state_dir / "reviews").glob("*.md")) if (state_dir / "reviews").exists() else []
    if reviews:
        parts.append("[최근 사후분석]\n" + reviews[-1].read_text(encoding="utf-8")[:2_000])
    return "\n\n".join(parts) or "(운용 기록 없음 — 개선안을 내지 마라)"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--proposer", default="codex", choices=["codex", "claude"])
    p.add_argument("--coder", default="opus", choices=["opus", "claude"])
    args = p.parse_args()

    summary = build_perf_summary(STATE_DIR)
    queue = ProposalQueue(
        base_dir=STATE_DIR / "proposals",
        repo_root=ROOT.parent,  # connect-ai 저장소 루트 (git apply 기준)
    )
    r = run_improve_cycle(
        summary, queue,
        proposer_brain=args.proposer, coder_brain=args.coder,
    )
    print(f"[improve] 개선안 {r['drafts']}건, 제출 {len(r['submitted'])}건, "
          f"자동거부 {len(r['rejected'])}건, 실패 {r['failed']}건")
    for cid in r["submitted"]:
        print(f"  카드 제출: {cid} (state/proposals/pending/{cid}.json)")
    for msg in r["rejected"]:
        print(f"  자동거부: {msg}")
    for e in r["errors"]:
        print(f"  error: {e[:200]}")
    if r["drafts"] == 0 and not r["errors"]:
        print("  (개선안 0건 — 정상)")

    pending = queue.list_pending()
    if pending:
        print(f"[improve] 심사 대기 카드 {len(pending)}건:")
        for c in pending:
            print(f"  - {c.id}: {c.proposal.splitlines()[0][:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
