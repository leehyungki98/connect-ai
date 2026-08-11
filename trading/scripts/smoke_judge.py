"""판정자 단독 스모크 — 판정 CLI + 출력 스키마 검증만 실호출로 확인.

smoke_two_stage 는 선정자가 제안을 0건 내면 판정자를 건너뛴다(비용 절감).
그래서 판정 경로가 실운용에서 한 번도 안 돌아본 채로 남는다. 이 스크립트는
결정적 프리스크린(K1/K2/K5)을 통과하도록 만든 합성 제안을 판정자에게 직접
넘겨, 실제 CLI 호출과 JSON 스키마 검증까지 태운다.

케이스:
  1. 정상 제안 → pass 또는 K3/K4 기각 중 하나가 나와야 한다
  2. 근거-데이터 모순 제안 → K3 로 기각되기를 기대 (강제는 아님)
  3. 보유 종목과 같은 종목 → K4 로 기각되기를 기대 (강제는 아님)

판정자는 "통과가 기본값"이라 2·3 이 통과해도 실패는 아니다. 이 스모크의
합격 기준은 판정이 유효한 스키마로 돌아오는가다.

사용법: python scripts/smoke_judge.py [--judge claude|codex]
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from autotrader.brain.client import Candidate, _run_cli  # noqa: E402
from autotrader.brain.schema import EntryDecision  # noqa: E402
from autotrader.brain.two_stage import (  # noqa: E402
    build_judge_prompt, deterministic_screen, extract_json, validate_judge_output,
)
from autotrader.gates.types import Portfolio, Position  # noqa: E402
from autotrader.screener.ranking import RankedSymbol  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--judge", default="claude", choices=["claude", "codex"])
    args = p.parse_args()

    # 프리스크린 통과 조건: 손익비 >= 1.2, 진입가 이탈 <= 15%, 20일 <= +40%
    cands = [
        Candidate(RankedSymbol("005930", 0.09, 0.08, 0.12, 0.018), 70_000),
        Candidate(RankedSymbol("000660", 0.08, 0.06, 0.11, 0.024), 250_000),
        Candidate(RankedSymbol("035720", 0.07, 0.05, 0.10, 0.021), 60_000),
    ]
    enters = [
        EntryDecision("005930", "enter", 70_000, 66_500, 78_000, 10,
                      "20일 +8%, 변동성 1.8%로 안정적 추세"),
        # K3 유도: 데이터는 +6% 인데 근거는 급등이라고 말한다
        EntryDecision("000660", "enter", 250_000, 237_000, 280_000, 10,
                      "20일 수익률이 +35% 로 급등 중이라 추세 강함"),
        # K4 유도: 보유 중인 종목과 동일
        EntryDecision("035720", "enter", 60_000, 57_000, 67_000, 10,
                      "20일 +5%, 눌림목 진입"),
    ]
    pf = Portfolio(10_000_000, 8_000_000, {"035720": Position(5, 300_000)})

    remaining, pre_rejected = deterministic_screen(enters, cands)
    print(f"[smoke] 프리스크린: 통과 {len(remaining)}건, 코드 기각 {len(pre_rejected)}건")
    for r in pre_rejected:
        print(f"  코드 기각: {r.symbol} [{r.criterion}] {r.reason}")
    if not remaining:
        print("[smoke] 판정 대상 0건 — 합성 제안을 손봐야 합니다.")
        return 1

    print(f"[smoke] 판정자={args.judge} 실호출 (대상 {len(remaining)}건)...")
    raw = _run_cli(args.judge, build_judge_prompt(remaining, cands, pf))
    ok, errs, verdicts = validate_judge_output(
        extract_json(raw), {d.symbol for d in remaining}
    )
    print(f"[smoke] 스키마 검증: {'통과' if ok else '실패'}")
    if not ok:
        for e in errs:
            print(f"  error: {e[:200]}")
        print(f"  raw head: {raw[:300]!r}")
        return 1
    for sym, (verdict, crit, reason) in sorted(verdicts.items()):
        mark = "통과" if verdict == "pass" else f"기각[{crit}]"
        print(f"  {sym}: {mark} — {reason[:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
