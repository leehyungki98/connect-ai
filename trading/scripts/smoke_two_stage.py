"""2단 브레인 실호출 스모크 (최소 비용: 선정자 1회 + 판정 대상 있을 때만 판정자 1회).

사용법: python scripts/smoke_two_stage.py [--proposer codex|claude] [--judge claude|codex]
사전 조건: 해당 CLI 로그인, codex는 ~/.codex/AGENTS.md 비대화형 예외 조항 필요.
"""
import argparse
import sys
from pathlib import Path

# Windows 콘솔(cp949)에서 em-dash 등이 섞이면 출력 중 UnicodeEncodeError로
# 죽어 정작 원인 메시지가 가려진다. 인코딩 불가 문자는 대체 표기로 흘린다.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autotrader.brain.client import Candidate
from autotrader.brain.two_stage import run_two_stage
from autotrader.gates.types import Portfolio, Position
from autotrader.screener.ranking import RankedSymbol


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--proposer", default="codex", choices=["codex", "claude"])
    p.add_argument("--judge", default="claude", choices=["claude", "codex"])
    args = p.parse_args()

    cands = [
        Candidate(RankedSymbol("005930", 0.096, 0.08, 0.12, 0.018), 70_000),
        Candidate(RankedSymbol("000660", 0.082, 0.06, 0.115, 0.024), 250_000),
    ]
    pf = Portfolio(10_000_000, 8_000_000, {"035720": Position(5, 600_000)})

    print(f"[smoke] 선정자={args.proposer}, 판정자={args.judge} 실호출...")
    r = run_two_stage(cands, pf, proposer_brain=args.proposer, judge_brain=args.judge)

    print(f"[smoke] ok={r.ok}")
    for d in r.entries:
        print(f"  통과 진입: {d.symbol} enter@{d.entry_price:,} "
              f"stop@{d.stop_price:,} target@{d.target_price:,} ({d.reason[:60]})")
    for rj in r.rejected:
        print(f"  기각: {rj.symbol} [{rj.criterion}] {rj.reason[:80]}")
    for e in r.exits:
        print(f"  청산 제안: {e.symbol} ({e.reason[:60]})")
    for err in r.errors:
        print(f"  error: {err[:300]}")
    if r.ok and not r.entries and not r.rejected:
        print("  (제안 0건 — 정상 케이스)")
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
