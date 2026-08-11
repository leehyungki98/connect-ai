"""미장 정세 정리 (윤가온) — 강해원이 채점한 근거를 엮어 쉬운 말로 답한다.

실행: python scripts/run_intel_brief.py ["질문"]
질문 없으면 "오늘 미장 정세 전반" 기본. 보유 성장주(NVDA/TSM/META) 기준.

이건 '정리 담당'의 온디맨드 답변 경로다 — 노유진의 고정 시각 리포트와 달리
사용자가 물을 때 그 질문에 맞춰 강해원 근거를 다시 엮는다. 매매 없음.
"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longcore import intel, intel_llm, organizer  # noqa: E402
from longcore.config import BUCKETS  # noqa: E402
from longcore.intel import quarter_of  # noqa: E402


def _held_growth() -> list:
    out = []
    for cfg in BUCKETS.values():
        out += list(cfg.get("sleeves", {}).get("GROWTH", {}))
    return list(dict.fromkeys(out))


def main() -> int:
    # 질문은 인자 또는 env(INTEL_BRIEF_Q). env 를 쓰는 이유: 확장에서 호출할 때
    # 한글·특수문자 인자가 셸을 거치며 잘리는 걸 피한다(코덱스 인자 잘림과 같은 부류).
    import os
    question = " ".join(sys.argv[1:]).strip() or os.environ.get("INTEL_BRIEF_Q", "").strip()
    today = date.today()
    quarter = quarter_of(today)

    blocks = []
    for tk in _held_growth():
        entries = intel.load_quarter(tk, quarter)
        if entries:
            blocks.append(organizer.ticker_block(tk, entries))
    if not blocks:
        print("정리할 정세 근거가 없어요 — 강해원 채점(run_intel_score)을 먼저 돌리세요.")
        return 0

    result = organizer.synthesize(question, blocks, runner=intel_llm._run_claude)
    print(organizer.render(result, asof=today.isoformat()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
