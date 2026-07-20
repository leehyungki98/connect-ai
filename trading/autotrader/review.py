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
) -> dict:
    state_dir = Path(state_dir)
    pf = kis.get_portfolio()

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
            out = state_dir / "reviews" / f"{today.isoformat()}.md"
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
    return f"""당신은 한국 주식 스윙 자동매매 시스템(모의투자)의 사후분석 모듈이다.
오늘 실행 기록을 회고하라. 주문 지시는 하지 마라 — 관찰과 개선점만.

[오늘 마감 스냅샷]
{json.dumps(snapshot, ensure_ascii=False, indent=2)}

[장 시작 전 실행 리포트]
{premarket or "(기록 없음)"}

다음 구조로 한국어 마크다운으로 답하라:
## 오늘 요약
## 판단 품질 (진입/청산/스킵 근거의 타당성)
## 리스크 관찰 (한도 대비 사용률, 우려점)
## 개선 후보 (다음 실행 전 점검할 것)"""
