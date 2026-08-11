"""LLM 브레인 실호출 스모크 테스트.

사용법: python scripts/smoke_brain.py [claude|codex]
합성 후보 2종목으로 실제 CLI를 1회 호출해 스키마 검증 통과 여부를 확인한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 콘솔(cp949)에서 LLM 유래 문자(em-dash 등) 출력 크래시 방지.
# 인코딩 불가 문자는 대체 표기로 흘린다 (콘솔 인코딩은 유지 — 한글 가독성).
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")


from autotrader.brain.client import Candidate, ask_brain
from autotrader.gates.types import Portfolio, Position
from autotrader.screener.ranking import RankedSymbol


def main():
    brain = sys.argv[1] if len(sys.argv) > 1 else "claude"
    cands = [
        Candidate(RankedSymbol("005930", 0.096, 0.08, 0.12, 0.018), 70_000),
        Candidate(RankedSymbol("000660", 0.082, 0.06, 0.115, 0.024), 250_000),
    ]
    pf = Portfolio(10_000_000, 8_000_000, {"035720": Position(5, 600_000)})

    print(f"[smoke] {brain} CLI 호출 중...")
    r = ask_brain(cands, pf, brain=brain)
    print(f"[smoke] ok={r.ok}")
    if r.ok:
        for d in r.output.decisions:
            print(f"  decision: {d}")
        for e in r.output.exits:
            print(f"  exit: {e}")
    else:
        for err in r.errors:
            print(f"  error: {err}")
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
