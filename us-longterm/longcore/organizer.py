"""윤가온 — 정리 담당(Synthesizer). 전문가가 던진 '결론+근거'를 질문에 맞게 엮어
   돈의 주인이 읽을 쉬운 말로 낸다.

역할 경계 (중요):
- **해석하지 않는다. 엮는다.** 전문가(강해원)가 근거를 말로 붙여 던지면, 윤가온은
  그 근거들을 골라 배열하고 쉬운 말로 옮긴다. 근거에 없는 사실·판단을 지어내면 안 된다.
- 매매·주문 없음. 이건 관찰의 전달 계층이다.
- 지능은 전문가에게, 윤가온은 편집·번역. 그래서 출력의 모든 문장이 던져진 근거로
  역추적돼야 한다 (grounding). 환각을 막는 유일한 장치다.

순수 로직만 여기 둔다(프롬프트 조립·검증·서술). LLM 호출·ledger 읽기는 스크립트가.
"""
from __future__ import annotations

import json
from typing import Callable

MOODS = ("좋음", "중립", "나쁨")


def ticker_block(ticker: str, entries: list) -> dict:
    """한 종목의 강해원 기록을 윤가온 입력 형태로 압축.

    entries: intel.load_quarter 결과 [{impact, thesis_axis, basis, ...}].
    긍정·부정 근거를 나눠 담는다 (같은 근거를 중복 제거 — 같은 뉴스가 여러 번 잡힌다).
    """
    pos, neg = {}, {}          # basis -> (impact, axis), dict 로 중복 근거 접기
    score = 0
    for e in entries:
        score += e["impact"]
        if e["impact"] > 0:
            pos.setdefault(e["basis"], (e["impact"], e["thesis_axis"]))
        elif e["impact"] < 0:
            neg.setdefault(e["basis"], (e["impact"], e["thesis_axis"]))
    return {
        "ticker": ticker, "score": score, "count": len(entries),
        "positives": [{"axis": a, "basis": b} for b, (_, a) in pos.items()],
        "negatives": [{"axis": a, "basis": b} for b, (_, a) in neg.items()],
    }


def build_prompt(question: str, blocks: list) -> str:
    """윤가온 프롬프트 — 근거만 쓰고 지어내지 말라는 규율을 박는다."""
    parts = []
    for b in blocks:
        lines = [f"### {b['ticker']} (점수 {b['score']:+d}, 뉴스 {b['count']}건)"]
        if b["positives"]:
            lines.append("긍정 근거:")
            lines += [f"  + [{p['axis']}] {p['basis']}" for p in b["positives"][:8]]
        if b["negatives"]:
            lines.append("부정 근거:")
            lines += [f"  - [{n['axis']}] {n['basis']}" for n in b["negatives"][:8]]
        parts.append("\n".join(lines))
    material = "\n\n".join(parts)
    q = question.strip() or "오늘 미장 정세 전반이 어떤지 정리해줘."

    return f"""당신은 미국주식 장기 데스크의 '정리 담당' 윤가온이다.
정세분석가(강해원)가 뉴스마다 매긴 근거를 아래에 준다. 이걸 **엮어서** 돈의 주인이
읽을 쉬운 말로 정리하라.

## 절대 규칙
- **아래 근거에 있는 내용만 쓴다.** 근거에 없는 사실·수치·전망을 지어내지 마라.
  모르면 "근거가 약하다"고 쓴다. 이게 제일 중요하다.
- **쉬운 말.** 전문용어·영어약어 금지. 점수 숫자는 참고만, 문장은 말로 풀어라.
- 종목마다 왜 분위기가 좋은지/나쁜지를 근거를 묶어 1~3문장. 긍정·부정이 같이 있으면
  둘 다 말하라 (예: "성장은 좋은데 경쟁이 심해진다").
- 아래에 없는 종목은 언급하지 마라.

## 사용자 질문
{q}

## 강해원이 던진 근거
{material}

## 출력 (JSON 만, 그 외 텍스트 금지)
{{"summary": "한두 문장 총평", "by_ticker": [{{"ticker": "종목코드", "mood": "좋음|중립|나쁨", "text": "쉬운 말 설명"}}]}}"""


def _extract_json(text: str) -> str:
    s, e = text.find("{"), text.rfind("}")
    return text[s:e + 1] if s != -1 and e > s else text


def synthesize(question: str, blocks: list,
               runner: Callable[[str], str]) -> dict:
    """윤가온 호출 → 검증된 {summary, by_ticker:[...]}.

    grounding 검증: 입력에 없던 종목이 by_ticker 에 나오면 버린다(환각 차단).
    파싱·호출 실패는 빈 결과 — 정리는 관찰 전달이라 터져도 파이프라인을 안 죽인다.
    """
    if not blocks:
        return {"summary": "", "by_ticker": []}
    known = {b["ticker"] for b in blocks}
    try:
        raw = runner(build_prompt(question, blocks))
        data = json.loads(_extract_json(raw))
    except (RuntimeError, ValueError, KeyError, TypeError):
        return {"summary": "", "by_ticker": []}
    summary = data.get("summary", "") if isinstance(data, dict) else ""
    out = []
    for t in (data.get("by_ticker", []) if isinstance(data, dict) else []):
        if not isinstance(t, dict):
            continue
        tk, txt, mood = t.get("ticker"), t.get("text"), t.get("mood")
        if tk not in known:                      # 환각 종목 — 버린다
            continue
        if not (isinstance(txt, str) and txt.strip()):
            continue
        out.append({"ticker": tk, "mood": mood if mood in MOODS else "중립",
                    "text": txt.strip()})
    return {"summary": summary.strip() if isinstance(summary, str) else "",
            "by_ticker": out}


def render(result: dict, asof: str = "") -> str:
    """구조화 결과 → 텔레그램/화면용 쉬운 말 텍스트."""
    if not result.get("by_ticker") and not result.get("summary"):
        return "🌏 오늘 정리할 정세 근거가 없어요. (강해원이 아직 채점 안 했을 수 있어요)"
    icon = {"좋음": "🟢", "중립": "⚪", "나쁨": "🔴"}
    L = [f"🌏 *미장 정세 정리 (윤가온){' · ' + asof if asof else ''}*"]
    if result.get("summary"):
        L.append("")
        L.append(result["summary"])
    for t in result["by_ticker"]:
        L.append("")
        L.append(f"{icon.get(t['mood'], '⚪')} *{t['ticker']}* — {t['text']}")
    return "\n".join(L)
