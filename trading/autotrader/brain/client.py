"""LLM 브레인 CLI 클라이언트 (claude / codex).

유일하게 비결정적인 구성요소. 절대규칙 3: 출력은 반드시
validate_brain_output(스키마 검증) → 리스크/컴플라이언스 게이트를 거친다.
검증 실패 시 ok=False — 호출측은 아무 주문도 내지 않고 이전 상태 유지.
재시도 없음(단순성 우선).
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from autotrader.brain.schema import ValidationResult, validate_brain_output
from autotrader.gates.types import Portfolio
from autotrader.screener.ranking import RankedSymbol

# codex는 ~/.codex/AGENTS.md의 스킬 선택 규칙 때문에 비대화형 실행에서도
# 스킬 메뉴를 반환할 때가 있다(프롬프트로 막아도 재발). --output-schema로
# 응답 형태를 강제하면 구조적으로 차단된다.
_CODEX_SCHEMA = Path(__file__).with_name("codex_output_schema.json")
# 호출 용도마다 응답 형태가 다르다. 매매 결정은 decisions/exits, 개선 제안은
# improvements, 판정은 verdicts. 스키마를 하나로 고정하면 나머지 용도가
# 구조적으로 실패한다 — 실제로 개선 사이클이 매번 0건이었던 원인이다.
IMPROVE_SCHEMA = Path(__file__).with_name("improve_output_schema.json")
JUDGE_SCHEMA = Path(__file__).with_name("judge_output_schema.json")

CLI_COMMANDS = {
    "claude": ["claude", "-p"],   # 프롬프트는 마지막 인자
    "opus": ["claude", "--model", "opus", "-p"],  # 코다리 (코드 diff 구현)
    "codex": ["codex", "exec", "--output-schema", str(_CODEX_SCHEMA)],
}
CLI_TIMEOUT_SEC = 300


@dataclass(frozen=True)
class Candidate:
    ranked: RankedSymbol
    last_close: int  # 최근 종가 (원)


def build_prompt(candidates: Sequence[Candidate], pf: Portfolio) -> str:
    """과업·출력 형식을 앞에, 데이터를 뒤에 둔다.

    codex 는 프롬프트의 첫 지시에 응답하고 끝내는 경향이 있다. 예전처럼
    "당신은 …판단 모듈이다" 라는 정체성 선언이 먼저 오면 그 정체성에만
    답하고 decisions 를 0건으로 돌려준다 (2026-07-20 실측: 1,392자
    프롬프트에서 맨 끝 지시 무시 + 0건). 명령형 과업을 맨 앞에 둔다.
    """
    held_lines = (
        "\n".join(
            f"- {sym}: {p.qty}주, 평가액 {p.value_krw:,}원"
            for sym, p in sorted(pf.positions.items())
        )
        or "- 없음"
    )
    cand_lines = "\n".join(
        f"- {c.ranked.symbol}: 종가 {c.last_close:,}원, "
        f"20일수익률 {c.ranked.ret20:+.1%}, 60일수익률 {c.ranked.ret60:+.1%}, "
        f"20일변동성 {c.ranked.vol20:.1%}"
        for c in candidates
    )
    return f"""[과업] 아래 후보 중 진입할 종목과 보유 종목 중 청산할 종목을 판단하라.
한국 주식 스윙 트레이딩(며칠~몇 주 보유) 기준이다.
후보 각각에 대해 enter 또는 skip 판단을 반드시 하나씩 내라.

규칙:
- 확신 없으면 skip. 진입 강요 없음.
- enter 시 entry_price/stop_price/target_price는 양의 정수(원), 0 < stop < entry < target.
- horizon_days는 1~30 정수. reason은 500자 이내.
- 후보에 없는 종목 진입 금지, 보유하지 않은 종목 청산 금지.
- 포지션 크기는 시스템이 정하므로 출력하지 마라.

아래 JSON 형식으로만 응답하라. JSON 외 텍스트 금지:
{{"decisions": [{{"symbol": "6자리코드", "action": "enter|skip", "entry_price": 정수, "stop_price": 정수, "target_price": 정수, "horizon_days": 정수, "reason": "근거"}}], "exits": [{{"symbol": "6자리코드", "reason": "근거"}}]}}
skip이면 가격/기간 필드는 null 또는 생략.

⚠️ 위에 명시된 키 외에는 **단 하나도 추가하지 마라**. 주석용 키("_" 등),
수량(qty), 신뢰도, 메모 전부 금지다. 검증기가 여분 키를 발견하면 제안
전체를 기각하므로 그날 매매가 통째로 사라진다.

[후보 종목]
{cand_lines}

[현재 보유]
{held_lines}

[계좌] 총평가 {pf.equity_krw:,}원, 현금 {pf.cash_krw:,}원"""


def extract_json(text: str) -> str:
    """응답에서 JSON 부분만 결정적으로 추출 (코드펜스/잡담 제거)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return text
    return text[start : end + 1]


def _run_cli(brain: str, prompt: str, schema: Path | None = None) -> str:
    """schema: codex 의 --output-schema 를 이 호출에 한해 교체한다.

    codex 는 스키마로 응답 형태를 강제하므로, 용도에 맞는 스키마를 주지 않으면
    프롬프트가 무엇을 요구하든 매매 결정 형식으로 답한다. claude 계열은
    스키마 인자가 없어 이 값을 무시한다.
    """
    argv = CLI_COMMANDS[brain]
    if brain == "codex" and schema is not None:
        argv = ["codex", "exec", "--output-schema", str(schema)]
    exe = shutil.which(argv[0])  # Windows에서 PATHEXT 적용 (codex.cmd 등)
    if exe is None:
        raise RuntimeError(f"{brain} CLI not found on PATH")

    # codex 는 Windows 에서 codex.CMD 배치 파일로 실행된다. 여러 줄 프롬프트를
    # 인자로 넘기면 cmd.exe 를 거치며 잘려서, 모델이 첫 줄만 보고 "요청을
    # 보내주세요" 하거나 스킬 선택 메뉴를 띄운다 (2026-07-20 실측: 후보 데이터가
    # 프롬프트에 있는데도 "데이터를 보내주시면"이라고 답했다).
    # `-` 를 주고 stdin 으로 넘기면 셸 파싱을 타지 않는다.
    if brain == "codex":
        cmd, stdin_text = [exe] + argv[1:] + ["-"], prompt
    else:
        cmd, stdin_text = [exe] + argv[1:] + [prompt], None
    proc = subprocess.run(
        cmd, input=stdin_text, capture_output=True, text=True,
        encoding="utf-8", errors="replace",  # Windows cp949 디코딩 오류 방지
        timeout=CLI_TIMEOUT_SEC,
        **({} if stdin_text is not None else {"stdin": subprocess.DEVNULL}),
    )
    if proc.returncode != 0:
        detail = " | ".join(
            x for x in (proc.stderr.strip(), proc.stdout.strip()) if x
        )[:500]
        # 인증 만료를 콕 집어 알린다 — 사흘째 조용히 죽어 있던 사고(2026-07-27) 재발 방지.
        # 이건 코드로 못 고친다(사람이 재로그인). 메시지가 명확해야 손이 간다.
        low = detail.lower()
        if brain == "codex" and ("token_invalidated" in low
                                 or "authentication token has been invalidated" in low
                                 or "401 unauthorized" in low):
            raise RuntimeError(
                "codex 인증 만료 — 터미널에서 `codex login` 재로그인 필요. "
                "(선정자 레오가 멈춘 상태. 재로그인 전까지 신규 진입 제안이 안 나온다) "
                f"| 원문: {detail[:200]}")
        raise RuntimeError(f"{brain} CLI failed (rc={proc.returncode}): {detail}")
    return proc.stdout


def ask_text(
    prompt: str,
    brain: str = "claude",
    runner: Callable[[str, str], str] = _run_cli,
) -> str:
    """자유 텍스트 호출 (사후분석 전용 — 주문 경로 아님, 스키마 검증 불필요)."""
    if brain not in CLI_COMMANDS:
        raise ValueError(f"unknown brain: {brain!r}")
    return runner(brain, prompt)


def ask_brain(
    candidates: Sequence[Candidate],
    pf: Portfolio,
    brain: str = "claude",
    runner: Callable[[str, str], str] = _run_cli,  # 테스트에서 모킹
) -> ValidationResult:
    if brain not in CLI_COMMANDS:
        return ValidationResult(False, (f"unknown brain: {brain!r}",), None)
    prompt = build_prompt(candidates, pf)
    try:
        raw = runner(brain, prompt)
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
        return ValidationResult(False, (f"CLI error: {e}",), None)
    return validate_brain_output(
        extract_json(raw),
        candidate_symbols={c.ranked.symbol for c in candidates},
        held_symbols=set(pf.positions.keys()),
    )
