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


# 보유 종목 분위기 급락 경보 임계 — 최근 창(일) 내 impact 합이 이 값 이하면 경고.
# 사용자 선택(2026-07-22): 분기 전체 추세만 보지 말고 분기 중이라도 급락하면 알림.
# ⚠ 시작 임계치다. 표본이 쌓이기 전엔 우연히 나쁜 며칠에도 울릴 수 있어, 데이터가
#    모이면 조정한다 (섀도 리포트의 '20건 미만 가설 승격 금지'와 같은 태도).
ALERT_WINDOW_DAYS = 14
ALERT_SCORE_THRESHOLD = -4


def recent_drop(entries: list, today: date, window_days: int = ALERT_WINDOW_DAYS,
                threshold: int = ALERT_SCORE_THRESHOLD) -> dict | None:
    """한 종목의 최근 창 내 부정 누적이 임계 이하면 경보 dict, 아니면 None.

    entries: 한 종목의 정세 기록(load_quarter 결과). date 문자열로 창을 자른다.
    분기 총점이 아니라 '최근 며칠'을 보는 이유 — 분기 초 호재가 말기 악재를 가려
    급락을 놓치면 안 된다. 매도/VOO 판단이 늦으면 손실이 실현된다.
    """
    from datetime import timedelta
    cutoff = (today - timedelta(days=window_days)).isoformat()
    recent = [e for e in entries if e.get("date", "") >= cutoff]
    score = sum(e["impact"] for e in recent)
    if not recent or score > threshold:
        return None
    negs = sorted((e for e in recent if e["impact"] < 0), key=lambda e: e["impact"])
    return {
        "window_days": window_days, "score": score, "count": len(recent),
        "worst": negs[:3],
    }


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
