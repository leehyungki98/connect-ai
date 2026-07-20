"""정세 기록 (강해원 담당) — 뉴스·매크로·지정학을 구조화해 분기 내내 적재한다.

**이 모듈은 매매를 발동시키지 않는다.** 논지 재판정의 방아쇠는 하드 데이터(실적)
뿐이다 (thesis.py). 여기 쌓인 점수의 용도는 DESKS.md 권한 경계 3 그대로:
  ① 분기 리뷰의 질적 리스크 서술
  ② 사후분석 해석
  ③ 긴급 이벤트 보고 → **사용자의** 킬스위치 판단
왜 분리하나: LLM 이 매긴 감성 점수는 같은 입력에도 값이 흔들려 재현·백테스트가
불가능하다. 그런 입력이 매도를 발동시키면 검증이라는 말이 성립하지 않는다.

저장 위치는 ledger/ (학습 자산 — 재생성 불가, git 추적).
"""
import json
from datetime import date
from pathlib import Path

from .store import LEDGER_DIR

IMPACT_MIN, IMPACT_MAX = -2, 2
THESIS_AXES = ("수익성", "성장", "밸류", "지정학", "규제", "경쟁")


def quarter_of(d: date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def record(ticker: str, event_date: date, source: str, event: str,
           impact: int, thesis_axis: str, basis: str,
           ledger_dir: Path = None) -> Path:
    """정세 항목 1건 적재. 값 검증은 여기서 — 쓰레기가 쌓이면 집계가 무의미해진다."""
    if not isinstance(impact, int) or not IMPACT_MIN <= impact <= IMPACT_MAX:
        raise ValueError(f"impact 는 {IMPACT_MIN}~{IMPACT_MAX} 정수: {impact!r}")
    if thesis_axis not in THESIS_AXES:
        raise ValueError(f"thesis_axis 는 {THESIS_AXES} 중 하나: {thesis_axis!r}")
    if not basis.strip():
        raise ValueError("basis(근거) 는 비울 수 없다 — 점수만 있고 근거 없는 기록 금지")

    base = Path(ledger_dir) if ledger_dir else LEDGER_DIR / "intel"
    path = base / ticker / f"{quarter_of(event_date)}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "date": event_date.isoformat(), "source": source, "event": event,
        "impact": impact, "thesis_axis": thesis_axis, "basis": basis,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def load_quarter(ticker: str, quarter: str, ledger_dir: Path = None) -> list:
    base = Path(ledger_dir) if ledger_dir else LEDGER_DIR / "intel"
    path = base / ticker / f"{quarter}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def summarize(entries: list) -> dict:
    """분기 집계 — 총점·건수·축별 분해. 판단이 아니라 재료다."""
    by_axis = {}
    for e in entries:
        by_axis[e["thesis_axis"]] = by_axis.get(e["thesis_axis"], 0) + e["impact"]
    negatives = [e for e in entries if e["impact"] < 0]
    return {
        "count": len(entries),
        "score": sum(e["impact"] for e in entries),
        "by_axis": dict(sorted(by_axis.items())),
        "worst": sorted(negatives, key=lambda e: e["impact"])[:3],
    }
