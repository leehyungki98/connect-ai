"""분석·개선 사이클: 운용 로그 요약 → GPT 개선안 → 코다리(Opus) diff → 변경 제안 큐.

사용법: python scripts/run_improve.py [--proposer codex|claude] [--coder opus|claude]
비용: 선정자 1콜 + 개선안 개수만큼 코다리 콜 (최대 3).
제출된 카드는 사용자가 심사한다 — 자동 승인 없음.
"""
import argparse
import json
import subprocess
import sys
from datetime import date
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

# 주간 사이클이므로 한 주 거래일만큼은 쌓여야 분석할 게 생긴다.
MIN_DAYS_FOR_IMPROVE = 5


def build_perf_summary(state_dir: Path, max_lines: int = 14,
                       ledger_dir: Path | None = None) -> str:
    parts = []
    eq = state_dir / "equity_log.jsonl"
    if eq.exists():
        lines = eq.read_text(encoding="utf-8").splitlines()[-max_lines:]
        parts.append("[평가액 로그 (최근)]\n" + "\n".join(lines))
    pm = state_dir / "premarket_log.jsonl"
    if pm.exists():
        lines = pm.read_text(encoding="utf-8").splitlines()[-3:]
        parts.append("[장 시작 전 실행 리포트 (최근 3회)]\n" + "\n".join(lines))
    rv = (ledger_dir / "reviews") if ledger_dir else (state_dir / "reviews")
    reviews = sorted(rv.glob("*.md")) if rv.exists() else []
    if reviews:
        parts.append("[최근 사후분석]\n" + reviews[-1].read_text(encoding="utf-8")[:2_000])
    return "\n\n".join(parts) or "(운용 기록 없음 — 개선안을 내지 마라)"


def commit_ledger(repo_root: Path, ledger_dir: Path) -> str:
    """ledger/ 만 주 1회 커밋한다 (백업 목적).

    - pathspec 커밋이라 사용자가 다른 파일을 staged 해둬도 휩쓸지 않는다.
    - push 는 하지 않는다. 이 저장소는 공개라 발행은 사용자 판단이어야 한다.
    - 백업이 본 작업을 깨면 안 되므로 모든 실패는 메시지만 남기고 넘어간다.
    """
    rel = ledger_dir.relative_to(repo_root).as_posix()
    try:
        changed = subprocess.run(
            ["git", "status", "--porcelain", "--", rel],
            cwd=repo_root, capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        if changed.returncode != 0:
            return f"git status 실패: {(changed.stderr or '').strip()[:120]}"
        if not changed.stdout.strip():
            return "변경 없음 — 커밋 생략"
        subprocess.run(["git", "add", "--", rel], cwd=repo_root,
                       capture_output=True, text=True, timeout=30, check=True,
                       encoding="utf-8", errors="replace")
        msg = f"chore(ledger): {date.today().isoformat()} 주간 학습 자산 백업"
        done = subprocess.run(["git", "commit", "-m", msg, "--", rel],
                              cwd=repo_root, capture_output=True, text=True, timeout=60,
                              encoding="utf-8", errors="replace")
        if done.returncode != 0:
            return f"커밋 실패: {(done.stderr or done.stdout or '').strip()[:160]}"
        n = len(changed.stdout.strip().splitlines())
        return f"커밋 완료 — 파일 {n}건 (push 안 함)"
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        return f"커밋 건너뜀: {e}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--proposer", default="codex", choices=["codex", "claude"])
    p.add_argument("--coder", default="opus", choices=["opus", "claude"])
    p.add_argument("--no-commit", action="store_true", help="ledger 백업 커밋 생략")
    p.add_argument("--force", action="store_true", help="운용 기록이 얇아도 강제 실행")
    args = p.parse_args()

    # 재료가 얇으면 선정자가 분석 대신 자기 역할 소개문을 뱉고, 코다리는
    # 구현할 게 없다며 거부한다 (2026-07-20 실측). LLM 두 번 부르고 쓰레기
    # 카드가 남을 뿐이라 아예 시작하지 않는다.
    pm = STATE_DIR / "premarket_log.jsonl"
    days = set()
    if pm.exists():
        for line in pm.read_text(encoding="utf-8").splitlines():
            try:
                days.add(json.loads(line)["date"])
            except (json.JSONDecodeError, KeyError):
                continue
    if len(days) < MIN_DAYS_FOR_IMPROVE and not args.force:
        print(f"[improve] 운용 기록 {len(days)}일 — {MIN_DAYS_FOR_IMPROVE}일 미만이라 건너뜁니다.")
        print("[improve] 재료가 쌓인 뒤 제안하는 게 맞습니다. 강제하려면 --force.")
        return 0

    summary = build_perf_summary(STATE_DIR, ledger_dir=ROOT / "ledger")
    queue = ProposalQueue(
        base_dir=STATE_DIR / "proposals",
        repo_root=ROOT.parent,  # connect-ai 저장소 루트 (git apply 기준)
    )
    r = run_improve_cycle(
        summary, queue,
        proposer_brain=args.proposer, coder_brain=args.coder,
        repo_root=ROOT.parent,  # 코다리가 대상 파일 원문을 보고 diff 를 쓰게 한다
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

    if not args.no_commit:
        print(f"[ledger] {commit_ledger(ROOT.parent, ROOT / 'ledger')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
