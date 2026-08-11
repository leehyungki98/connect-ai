"""윤가온(정리 담당)의 스윙 쪽 두뇌 — 범용 엮기.

미장(us-longterm/longcore/organizer.py)은 강해원 점수를 종목별로 엮지만, 스윙 재료는
성격이 다르다: 현빈 사후분석(이미 글) + 시장 폭 + 섀도 실패패턴 + 최근 선정. 그래서
'근거 블록'을 일반형으로 받아 질문에 맞게 엮는다.

경계·규율은 미장과 동일:
- 해석하지 않고 엮는다. 준 근거에 있는 것만 쓰고, 없으면 "근거가 약하다"고 쓴다.
- 매매·주문 없음. 관찰의 전달 계층.
- 두 데스크가 별도 패키지라 최소 로직을 여기 재정의한다(전략 상수처럼). 지능은
  전문가(현빈 등)에게 있고 윤가온은 편집·번역이다.
"""
from __future__ import annotations

import json
from typing import Callable


def build_prompt(question: str, blocks: list) -> str:
    """blocks: [{source, facts:[str]}]. 근거를 출처별로 묶어 제시."""
    parts = []
    for b in blocks:
        facts = "\n".join(f"  · {f}" for f in b["facts"] if str(f).strip())
        if facts:
            parts.append(f"### {b['source']}\n{facts}")
    material = "\n\n".join(parts)
    q = question.strip() or "스윙 전략이 지금 어떻게 돌아가는지 정리해줘."

    return f"""당신은 한국 주식 스윙 데스크의 '정리 담당' 윤가온이다.
아래는 스윙팀 전문가·기록에서 뽑은 근거다. 이걸 **엮어서** 돈의 주인이 읽을 쉬운
말로 정리하라.

## 절대 규칙
- **아래 근거에 있는 내용만 쓴다.** 없는 사실·수치·전망을 지어내지 마라. 모르면
  "근거가 약하다"고 쓴다. 이게 제일 중요하다.
- **쉬운 말.** 전문용어·영어약어 금지(예: R:R·bp·MDD·샤프 금지). 숫자는 풀어써라.
- 질문에 곧장 답하고, 왜 그런지 근거를 붙여라. 좋은 점·나쁜 점이 같이 있으면 둘 다.
- 매매 지시·수익 보장은 하지 마라. 이건 현황 설명이지 권유가 아니다.

## 사용자 질문
{q}

## 근거
{material}

## 출력 (JSON 만, 그 외 텍스트 금지)
{{"answer": "쉬운 말 정리 (3~8문장)"}}"""


def _extract_json(text: str) -> str:
    s, e = text.find("{"), text.rfind("}")
    return text[s:e + 1] if s != -1 and e > s else text


def synthesize(question: str, blocks: list,
               runner: Callable[[str], str]) -> dict:
    """윤가온 호출 → {answer}. 재료 없으면 호출 안 함. 실패는 빈 answer(관찰이라 fail-open)."""
    blocks = [b for b in blocks if b.get("facts")]
    if not blocks:
        return {"answer": ""}
    try:
        raw = runner(build_prompt(question, blocks))
        data = json.loads(_extract_json(raw))
        ans = data.get("answer", "") if isinstance(data, dict) else ""
    except (RuntimeError, ValueError, KeyError, TypeError):
        return {"answer": ""}
    return {"answer": ans.strip() if isinstance(ans, str) else ""}


def render(result: dict, asof: str = "") -> str:
    ans = (result or {}).get("answer", "").strip()
    if not ans:
        return "🧩 정리할 스윙 근거가 부족해요. (아직 기록이 얕을 수 있어요)"
    head = f"🧩 *스윙 전략 정리 (윤가온){' · ' + asof if asof else ''}*"
    return f"{head}\n\n{ans}"
