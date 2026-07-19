"""분석·개선 루프: GPT 개선안 제안 → 코다리(Opus) diff 구현 → 변경 제안 큐 제출.

조직 역할 (스펙):
- GPT(codex): 성과·기각 로그 요약을 보고 전략 개선안을 넉넉히 제시 (최대 3건).
- 코다리(claude --model opus): 개선안 하나를 실제 unified diff로 구현하고
  "지시를 이렇게 해석해 이렇게 구현했다" 요약을 붙인다.
- 제출된 카드는 변경 제안 큐(proposals.py)가 심사: 안전층 diff 자동 거부,
  승인 시 적용 → 전체 테스트 통과해야 유지. 사용자만 승인할 수 있다.

두 LLM 출력 모두 결정적 검증. 무효면 그 항목은 버린다(카드 미제출, fail-closed).
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Callable

from autotrader.brain.client import _run_cli, extract_json
from autotrader.proposals import ActionResult, ProposalQueue

MAX_IMPROVEMENTS = 3
MAX_FIELD_LEN = 1_000

FORBIDDEN_NOTE = """편집 금지 구역 (건드리면 자동 거부된다):
- trading/autotrader/safety/  (킬스위치·일일손실한도)
- trading/autotrader/gates/   (리스크·컴플라이언스 게이트)
- trading/autotrader/config.py (리스크 한도 상수)"""


@dataclass(frozen=True)
class ImproveDraft:
    analysis: str
    diagnosis: str
    proposal: str


# ── 1단: GPT 개선안 제안 ──────────────────────────────────────────────

def build_improve_prompt(perf_summary: str) -> str:
    return f"""당신은 한국 주식 스윙 자동매매 팀의 '선정·제안자'다.
아래 최근 운용 기록을 보고 시스템 개선안을 제시하라. 목표는 리스크조정
수익(샤프)과 MDD 개선이지 절대수익이 아니다. 근거 없는 개선안은 내지 마라.
개선할 게 없으면 빈 배열이 정상이다.

[최근 운용 기록]
{perf_summary}

{FORBIDDEN_NOTE}

최대 {MAX_IMPROVEMENTS}건, 아래 JSON 형식으로만 응답하라. JSON 외 텍스트 금지:
{{"improvements": [{{"analysis": "데이터에서 관찰한 사실", "diagnosis": "원인 진단", "proposal": "구체적 변경 제안 (파일/파라미터 수준)"}}]}}"""


def validate_improvements(raw: str) -> tuple[tuple[ImproveDraft, ...], tuple[str, ...]]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        return (), (f"invalid JSON: {e}",)
    if not isinstance(data, dict) or set(data.keys()) != {"improvements"}:
        return (), ("root must have exactly 'improvements'",)
    items = data["improvements"]
    if not isinstance(items, list) or len(items) > MAX_IMPROVEMENTS:
        return (), (f"improvements must be a list of <= {MAX_IMPROVEMENTS}",)
    drafts, errors = [], []
    for i, it in enumerate(items):
        if not isinstance(it, dict) or set(it.keys()) != {"analysis", "diagnosis", "proposal"}:
            errors.append(f"improvements[{i}]: keys must be analysis/diagnosis/proposal")
            continue
        vals = [it["analysis"], it["diagnosis"], it["proposal"]]
        if not all(isinstance(v, str) and 1 <= len(v) <= MAX_FIELD_LEN for v in vals):
            errors.append(f"improvements[{i}]: fields must be non-empty strings <= {MAX_FIELD_LEN}")
            continue
        drafts.append(ImproveDraft(*vals))
    return tuple(drafts), tuple(errors)


def propose_improvements(
    perf_summary: str,
    brain: str = "codex",
    runner: Callable[[str, str], str] | None = None,
) -> tuple[tuple[ImproveDraft, ...], tuple[str, ...]]:
    run = runner or _run_cli
    try:
        raw = run(brain, build_improve_prompt(perf_summary))
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
        return (), (f"improve CLI error: {e}",)
    return validate_improvements(extract_json(raw))


# ── 2단: 코다리 diff 구현 ─────────────────────────────────────────────

_DIFF_FENCE_RE = re.compile(r"```diff\n(.*?)```", re.DOTALL)


def build_coder_prompt(draft: ImproveDraft) -> str:
    return f"""당신은 자동매매 팀의 코드 담당 '코다리'다. 아래 승인 대기 개선안을
실제 코드 diff로 구현하라.

[분석] {draft.analysis}
[진단] {draft.diagnosis}
[제안] {draft.proposal}

규칙:
- 제안에 직접 연결된 라인만 수정하라 (surgical). 무관한 코드·주석·포맷 변경 금지.
- {FORBIDDEN_NOTE}
- 경로는 저장소 루트 기준 (예: trading/autotrader/screener/ranking.py).

응답 형식 (이 순서 그대로):
1. "지시를 이렇게 해석해 이렇게 구현했다" 요약 (3문장 이내)
2. ```diff 펜스 안에 git unified diff (diff --git a/... b/... 형식) 하나"""


def extract_coder_output(raw: str) -> tuple[str, str] | None:
    """(요약, diff) 반환. diff 펜스가 없으면 None."""
    m = _DIFF_FENCE_RE.search(raw)
    if not m or not m.group(1).strip():
        return None
    summary = (raw[: m.start()].strip() or "(요약 없음)")[:500]
    return summary, m.group(1)


def implement_and_submit(
    draft: ImproveDraft,
    queue: ProposalQueue,
    brain: str = "opus",
    runner: Callable[[str, str], str] | None = None,
) -> ActionResult | None:
    """코다리 호출 → diff 추출 → 큐 제출. diff 추출 실패 시 None (제출 없음)."""
    run = runner or _run_cli
    try:
        raw = run(brain, build_coder_prompt(draft))
    except (RuntimeError, subprocess.TimeoutExpired, OSError):
        return None
    out = extract_coder_output(raw)
    if out is None:
        return None
    summary, diff = out
    return queue.submit(
        draft.analysis, draft.diagnosis,
        f"{draft.proposal}\n\n[코다리 구현 요약] {summary}", diff,
    )


# ── 전체 사이클 ───────────────────────────────────────────────────────

def run_improve_cycle(
    perf_summary: str,
    queue: ProposalQueue,
    proposer_brain: str = "codex",
    coder_brain: str = "opus",
    proposer_runner: Callable | None = None,
    coder_runner: Callable | None = None,
) -> dict:
    """반환: {"drafts": n, "submitted": [...], "rejected": [...], "failed": n, "errors": [...]}"""
    drafts, errors = propose_improvements(perf_summary, proposer_brain, proposer_runner)
    report = {"drafts": len(drafts), "submitted": [], "rejected": [],
              "failed": 0, "errors": list(errors)}
    for d in drafts:
        r = implement_and_submit(d, queue, coder_brain, coder_runner)
        if r is None:
            report["failed"] += 1
        elif r.ok:
            report["submitted"].append(r.card.id)
        else:
            report["rejected"].append(f"{r.card.id}: {r.message}")
    return report
