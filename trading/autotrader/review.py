"""마감 후 사후분석 (관찰 전용 — 주문 경로 아님).

1. 평가액 스냅샷을 equity_log.jsonl에 기록 (일일 수익률은 관찰 지표일 뿐,
   의사결정 목표가 아니다 — 스펙 성과 원칙)
2. (선택) LLM 사후분석: 당일 리포트를 자유 텍스트로 회고, reviews/에 저장.
   실패해도 스냅샷 기록에는 영향 없음.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Callable

from autotrader.brain.client import ask_text


def _upsert_equity_log(log: Path, snapshot: dict) -> None:
    """같은 날짜 항목은 덮어쓴다.

    append 만 하면 하루에 두 번 마감을 돌릴 때 같은 날짜가 중복 적재되고,
    집계·백테스트 비교에서 그 날 가중치가 배로 잡힌다. 날짜가 키다.
    깨진 줄은 건드리지 않고 그대로 보존한다 (관찰 로그라 fail-open).
    """
    lines: list[str] = []
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                if json.loads(line).get("date") == snapshot["date"]:
                    continue  # 오늘 것은 아래에서 새로 쓴다
            except json.JSONDecodeError:
                pass  # 파싱 불가한 줄은 보존
            lines.append(line)
    lines.append(json.dumps(snapshot, ensure_ascii=False))
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_postmarket(
    kis,
    state_dir: Path,
    today: date,
    brain: str = "claude",
    runner: Callable | None = None,
    llm_review: bool = False,
    review_dir: Path | None = None,
    allocation_krw: int | None = None,
) -> dict:
    """review_dir: 사후분석 저장 위치. 기본은 state_dir/reviews (하위호환).

    운영 스크립트는 trading/ledger/reviews 를 넘긴다 — 사후분석은 재생성이
    안 되는 학습 자산이라 커밋 금지 구역인 state/ 밖에 두고 git 으로 남긴다.

    allocation_krw: 데스크 배분액. day_start(프리마켓)가 데스크 지분(250만)으로
    기록되므로, 여기서도 같은 기준으로 스케일해야 일일수익률이 맞는다. None 이면
    기본 SWING_ALLOCATION_KRW 를 쓴다. 이 스케일이 없으면 실계좌(1,000만) 대비
    day_start(250만)를 나눠 +300% 같은 거짓 수익률이 찍힌다 (2026-07-27 실제 사고).
    """
    state_dir = Path(state_dir)
    pf = kis.get_portfolio()
    # 프리마켓과 동일 기준으로 — 데스크 지분으로 축소. (0 이면 스케일 생략: 테스트용)
    from autotrader.config import SWING_ALLOCATION_KRW
    alloc = SWING_ALLOCATION_KRW if allocation_krw is None else allocation_krw
    if alloc:
        from autotrader.pipeline import desk_portfolio
        pf = desk_portfolio(pf, alloc)

    # 일일 수익률 (관찰용) — day_start 기록이 오늘 것일 때만 계산
    daily_return_bp = None
    ds_file = state_dir / "day_start.json"
    if ds_file.exists():
        try:
            raw = json.loads(ds_file.read_text(encoding="utf-8"))
            if str(raw["date"]) == today.isoformat() and int(raw["equity"]) > 0:
                start = int(raw["equity"])
                daily_return_bp = (pf.equity_krw - start) * 10_000 // start
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
            pass  # 관찰 지표라 fail-open — 기록만 생략

    snapshot = {
        "date": today.isoformat(),
        "equity_krw": pf.equity_krw,
        "cash_krw": pf.cash_krw,
        "n_positions": len(pf.positions),
        "daily_return_bp": daily_return_bp,
    }
    log = state_dir / "equity_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    _upsert_equity_log(log, snapshot)

    summary = {**snapshot, "review_file": None, "review_error": None}
    if llm_review:
        try:
            prompt = _build_review_prompt(state_dir, today, snapshot)
            text = ask_text(prompt, brain=brain, **({"runner": runner} if runner else {}))
            out = (review_dir or state_dir / "reviews") / f"{today.isoformat()}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            summary["review_file"] = str(out)
        except (RuntimeError, OSError, ValueError) as e:
            summary["review_error"] = str(e)  # 리뷰 실패는 치명적이지 않음
    return summary


def _build_review_prompt(state_dir: Path, today: date, snapshot: dict) -> str:
    premarket = ""
    log = state_dir / "premarket_log.jsonl"
    if log.exists():
        lines = log.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("date") == today.isoformat():
                premarket = json.dumps(rec, ensure_ascii=False, indent=2)
                break
    return f"""당신은 한국 주식 모의 자동매매 데스크의 사후분석 담당이다.
읽는 사람은 **개발자가 아니라 이 돈의 주인**이다. 코드를 모른다고 가정하라.

[오늘 마감 스냅샷]
{json.dumps(snapshot, ensure_ascii=False, indent=2)}

[장 시작 전 실행 리포트]
{premarket or "(기록 없음)"}

## 쓰는 방식 (엄수)
- **쉬운 말.** 전문용어·영어 약어 금지. 꼭 써야 하면 괄호로 풀어라.
  나쁜 예: "R:R 1.62", "0bp", "blocked: null", "MDD", "샤프"
  좋은 예: "손절 대비 목표가 1.6배", "손익 0%", "강제중단 없었음"
- **개발 얘기 금지.** 커밋·함수명·파일경로·테스트 통과 여부는 쓰지 마라.
  그건 이 사람 일이 아니다.
- **짧게.** 각 항목 1~2문장. 전체 15줄 이내.
- **추측은 추측이라고.** 확실치 않으면 "~로 보인다"라고 쓰고 근거를 대라.
- 주문 지시는 하지 마라. 파라미터 변경 제안도 하지 마라(백테스트·승인 필요).

## 다음 구조로 한국어로 답하라

### 오늘 무슨 일이 있었나
2~3문장. 매매했는지, 안 했으면 왜 안 했는지. 어제와 뭐가 달라졌는지.

### 잘 돌아갔나
안전장치(강제중단·손실한도)가 정상이었는지, 이상 징후가 있었는지 1~2문장.
문제 없으면 "이상 없음" 한 줄로 끝내라.

### 눈여겨볼 것
사람이 알아야 할 것만 **최대 3개**, 각 1문장. 없으면 "없음".
개발·테스트 얘기 말고, 돈·전략·시장에 관한 것만."""
