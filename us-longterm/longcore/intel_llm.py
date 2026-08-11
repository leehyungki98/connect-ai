"""정세분석가(LLM) 자동 채점 — 뉴스 한 건을 관련 티커별 impact/축/근거로 수치화.

⚠ 이건 **관찰**이다. 체결·리밸런싱 경로에는 LLM 이 절대 못 들어간다(그쪽은 100%
   결정적 코드 + 사람 승인). 여기서 나온 점수는 사람의 매도/VOO 판단 참고자료일 뿐이며,
   스윙팀 사후분석(영숙)이 LLM 인 것과 같은 층이다.

설계 원칙:
- **stdin 으로만 넘긴다.** Windows 에서 여러 줄 프롬프트를 인자로 주면 cmd.exe 를
  거치며 잘려 모델이 첫 줄만 본다 (스윙팀에서 실측된 사고 — codex 가 데이터를 받고도
  "데이터를 보내달라"고 답했다). 그래서 프롬프트는 항상 stdin.
- **출력은 intel.record() 검증을 그대로 통과해야 한다.** impact 정수 -2~+2, 축은
  고정 6종, 근거 필수. 어긋난 항목은 버린다 — 쓰레기가 쌓이면 집계가 무의미해진다.
- **runner 주입 가능.** 테스트는 네트워크·CLI 없이 가짜 runner 로 검증한다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Callable

from .intel import IMPACT_MAX, IMPACT_MIN, THESIS_AXES

CLI_TIMEOUT_SEC = 300
BATCH_SIZE = 10           # 한 번의 LLM 호출에 묶는 기사 수 (호출 수 ≈ 건수/10)


def _run_claude(prompt: str) -> str:
    """claude CLI 를 stdin 으로 호출. 실패는 RuntimeError."""
    exe = shutil.which("claude")
    if exe is None:
        raise RuntimeError("claude CLI not found on PATH")
    proc = subprocess.run(
        [exe, "-p", "-"], input=prompt, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=CLI_TIMEOUT_SEC,
    )
    if proc.returncode != 0:
        detail = " | ".join(x for x in (proc.stderr.strip(), proc.stdout.strip()) if x)[:500]
        raise RuntimeError(f"claude CLI failed (rc={proc.returncode}): {detail}")
    return proc.stdout


def _extract_json(text: str) -> str:
    """응답에서 최외곽 JSON 만 추출 (코드펜스·잡담 제거).

    먼저 나오는 여는 괄호를 기준으로 잡는다 — {"scores":[...]} 처럼 객체가 배열을
    품은 경우 안쪽 [ 를 먼저 잡으면 배열만 떼어내 스키마가 깨진다(실측).
    """
    candidates = [(text.find(op), op, cl) for op, cl in (("{", "}"), ("[", "]"))
                  if text.find(op) != -1]
    if not candidates:
        return text
    _, op, cl = min(candidates)          # 가장 먼저 등장하는 여는 괄호
    return text[text.find(op):text.rfind(cl) + 1]


def build_prompt(articles: list) -> str:
    """articles: [{idx, title, summary, tickers:[...]}]. 관련 티커별 채점을 요구."""
    axes = " / ".join(THESIS_AXES)
    lines = []
    for a in articles:
        lines.append(f"[{a['idx']}] 관련종목 {','.join(a['tickers'])}\n"
                     f"    제목: {a['title']}\n"
                     f"    요약: {a['summary'][:300]}")
    body = "\n".join(lines)
    return f"""당신은 미국주식 장기투자 데스크의 '정세분석가'다.
아래 뉴스 각각을, 표시된 관련종목마다 논지 영향으로 수치화하라.

## 규칙 (엄수)
- impact: 정수 {IMPACT_MIN}~{IMPACT_MAX}. -2 매우부정 / -1 부정 / 0 중립·무관 / +1 긍정 / +2 매우긍정.
  기사가 그 종목의 장기 투자논지에 주는 영향만 본다. 단순 주가 등락·루머는 0 에 가깝게.
- thesis_axis: 반드시 다음 중 하나 — {axes}.
  수익성=마진·이익, 성장=매출·수요·점유, 밸류=밸류에이션, 지정학=수출규제·관세·지정학,
  규제=반독점·정책, 경쟁=경쟁사·대체재.
- basis: 한 문장 근거 (한국어, 비어있으면 안 됨). 기사에 있는 사실만. 추측 금지.
- 관련종목이 여럿이면 각각 다르게 볼 수 있다 (같은 기사가 A엔 +2, B엔 -1).
- 확실치 않으면 impact 0, basis 에 "판단근거 약함"이라고 적어라. 억지 점수 금지.

## 뉴스
{body}

## 출력 (JSON 만, 그 외 텍스트 금지)
{{"scores": [{{"idx": 정수, "ticker": "종목코드", "impact": 정수, "thesis_axis": "축", "basis": "근거"}}]}}
각 뉴스의 관련종목마다 한 줄씩. idx 는 위 대괄호 번호."""


def _valid(entry: dict) -> bool:
    """intel.record() 와 같은 잣대 — 하나라도 어기면 버린다."""
    return (isinstance(entry.get("idx"), int)
            and isinstance(entry.get("ticker"), str) and entry["ticker"]
            and isinstance(entry.get("impact"), int)
            and IMPACT_MIN <= entry["impact"] <= IMPACT_MAX
            and entry.get("thesis_axis") in THESIS_AXES
            and isinstance(entry.get("basis"), str) and entry["basis"].strip())


def score_batch(articles: list, runner: Callable[[str], str] = _run_claude) -> list:
    """한 배치를 채점 → 검증 통과한 [{idx, ticker, impact, thesis_axis, basis}].

    파싱·검증 실패는 조용히 버린다 (그 배치만 손실, 다음 배치는 계속). 채점은
    관찰 보조라 한 배치가 깨져도 파이프라인 전체를 멈추면 안 된다.
    """
    if not articles:
        return []
    try:
        raw = runner(build_prompt(articles))
        data = json.loads(_extract_json(raw))
        scores = data.get("scores", []) if isinstance(data, dict) else []
    except (RuntimeError, ValueError, KeyError, TypeError):
        return []
    valid_idx = {a["idx"] for a in articles}
    tickers_of = {a["idx"]: set(a["tickers"]) for a in articles}
    out = []
    for e in scores:
        if not _valid(e) or e["idx"] not in valid_idx:
            continue
        # 배치에 없던 티커를 지어내면 버린다 (환각 방지)
        if e["ticker"] not in tickers_of[e["idx"]]:
            continue
        out.append({k: e[k] for k in ("idx", "ticker", "impact", "thesis_axis", "basis")})
    return out
