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
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from autotrader.brain.client import IMPROVE_SCHEMA, _run_cli, extract_json
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
    # 사용자가 코드를 안 보고도 판단할 수 있게 하는 필드. 없으면 빈 문자열.
    title: str = ""
    risk: str = ""
    tradeoff: str = ""


# ── 1단: GPT 개선안 제안 ──────────────────────────────────────────────

def build_improve_prompt(perf_summary: str) -> str:
    """과업·출력 형식을 앞에, 재료를 뒤에 둔다.

    codex 는 긴 프롬프트의 앞부분만 처리하고 뒤를 흘린다 (2026-07-20 실측:
    3,271자 프롬프트에서 맨 끝 지시를 무시했고, 같은 지시를 맨 앞에 두자
    그대로 따랐다). 페르소나가 먼저이고 출력 형식이 맨 끝이면 "저는
    선정자입니다" 같은 자기소개만 돌아온다.
    """
    return f"""[과업] 아래 운용 기록을 근거로 시스템 개선안을 최대 {MAX_IMPROVEMENTS}건 제시하라.
목표는 리스크조정 수익(샤프)과 MDD 개선이지 절대수익이 아니다.
근거 없는 개선안은 내지 마라. 개선할 게 없으면 빈 배열이 정상이다.
반드시 아래 운용 기록의 구체적 수치·파일·사건을 인용하라. 역할 소개·계획
선언은 개선안이 아니다.

[출력] 아래 JSON 형식으로만 응답. JSON 외 텍스트 금지:
{{"improvements": [{{"title": "한 줄 제목 (30자 이내, 경로·함수명 금지)", "analysis": "데이터에서 관찰한 사실", "diagnosis": "원인 진단", "proposal": "구체적 변경 제안 (파일/파라미터 수준)", "risk": "승인하지 않으면 남는 위험", "tradeoff": "승인했을 때의 단점·부작용·되돌리기 비용"}}]}}

사용자는 이 카드만 보고 승인 여부를 정한다. 코드를 몰라도 판단할 수 있게
써라. tradeoff 에 "없음"은 금지 — 되돌리기 비용이라도 적어라.

{FORBIDDEN_NOTE}

[최근 운용 기록]
{perf_summary}"""


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
        required = {"analysis", "diagnosis", "proposal"}
        optional = {"title", "risk", "tradeoff"}
        if not isinstance(it, dict) or not required <= set(it.keys()) or set(it.keys()) - (required | optional):
            errors.append(f"improvements[{i}]: keys must be analysis/diagnosis/proposal (+title/risk/tradeoff)")
            continue
        vals = [it["analysis"], it["diagnosis"], it["proposal"]]
        if not all(isinstance(v, str) and 1 <= len(v) <= MAX_FIELD_LEN for v in vals):
            errors.append(f"improvements[{i}]: fields must be non-empty strings <= {MAX_FIELD_LEN}")
            continue
        extras = {k: it.get(k, "") for k in optional}
        if not all(isinstance(v, str) and len(v) <= MAX_FIELD_LEN for v in extras.values()):
            errors.append(f"improvements[{i}]: title/risk/tradeoff must be strings <= {MAX_FIELD_LEN}")
            continue
        drafts.append(ImproveDraft(*vals, **extras))
    return tuple(drafts), tuple(errors)


def propose_improvements(
    perf_summary: str,
    brain: str = "codex",
    runner: Callable[[str, str], str] | None = None,
) -> tuple[tuple[ImproveDraft, ...], tuple[str, ...]]:
    # codex 기본 스키마는 매매 결정용이라 improvements 를 낼 수 없다.
    run = runner or (lambda b, pr: _run_cli(b, pr, schema=IMPROVE_SCHEMA))
    try:
        raw = run(brain, build_improve_prompt(perf_summary))
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
        return (), (f"improve CLI error: {e}",)
    return validate_improvements(extract_json(raw))


# ── 2단: 코다리 diff 구현 ─────────────────────────────────────────────

_DIFF_FENCE_RE = re.compile(r"```diff\n(.*?)```", re.DOTALL)


_PATH_RE = re.compile(r"[\w./-]*trading/[\w./-]+\.py")
MAX_FILE_CHARS = 12_000


def collect_referenced_files(draft: ImproveDraft, repo_root: Path) -> str:
    """제안이 언급한 파일의 현재 내용을 줄번호와 함께 싣는다.

    unified diff 는 문맥 줄이 원본과 글자 단위로 일치해야 적용된다. 파일을
    안 보여주면 코다리가 기억으로 추측해서 쓰고, git apply 가 'corrupt patch'
    또는 'patch failed' 로 거부한다 (2026-07-20 첫 실제 카드가 이렇게 실패).
    """
    seen, blocks = set(), []
    for raw in _PATH_RE.findall(f"{draft.proposal}\n{draft.analysis}\n{draft.diagnosis}"):
        rel = raw[raw.index("trading/"):]
        if rel in seen:
            continue
        seen.add(rel)
        f = repo_root / rel
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        numbered = "\n".join(f"{i:4d}| {ln}" for i, ln in enumerate(text.splitlines(), 1))
        blocks.append(f"--- {rel} ---\n{numbered[:MAX_FILE_CHARS]}")
    return "\n\n".join(blocks) or "(제안에서 파일 경로를 찾지 못했다)"


def build_coder_prompt(draft: ImproveDraft, repo_root: Path | None = None) -> str:
    files = collect_referenced_files(draft, repo_root) if repo_root else "(파일 미제공)"
    return f"""당신은 자동매매 팀의 코드 담당 '코다리'다. 아래 승인 대기 개선안을
실제 코드 diff로 구현하라.

[분석] {draft.analysis}
[진단] {draft.diagnosis}
[제안] {draft.proposal}

[대상 파일의 현재 내용 — 문맥 줄은 반드시 여기서 그대로 옮겨라]
{files}

규칙:
- 제안에 직접 연결된 라인만 수정하라 (surgical). 무관한 코드·주석·포맷 변경 금지.
- {FORBIDDEN_NOTE}
- 경로는 저장소 루트 기준 (예: trading/autotrader/screener/ranking.py).

응답 형식 (이 순서 그대로):
1. "지시를 이렇게 해석해 이렇게 구현했다" 요약 (3문장 이내)
2. 수정한 파일마다 아래 펜스를 하나씩. 바뀐 부분만이 아니라 **파일 전체**를
   담아라. 위 원문에서 손대지 않은 줄은 글자 그대로 옮겨라.

```file:trading/autotrader/example.py
(파일 전체 내용)
```

⚠️ diff 를 직접 쓰지 마라. diff 는 이 시스템이 파일 내용을 비교해 만든다.
@@ 줄 수나 문맥을 손으로 맞추려다 틀리는 사고를 없애기 위한 것이다."""


_FILE_FENCE_RE = re.compile(r"```file:(\S+)\n(.*?)```", re.DOTALL)


def extract_coder_files(raw: str) -> tuple[str, list[tuple[str, str]]]:
    """(요약, [(경로, 새 내용), ...]) 반환. 파일 펜스가 없으면 빈 목록."""
    hits = list(_FILE_FENCE_RE.finditer(raw))
    if not hits:
        return (raw[:500].strip() or "(요약 없음)"), []
    summary = (raw[: hits[0].start()].strip() or "(요약 없음)")[:500]
    files = []
    for m in hits:
        path = m.group(1).strip().lstrip("./")
        body = m.group(2)
        if path and body.strip():
            files.append((path, body))
    return summary, files


def make_diff(repo_root: Path, rel_path: str, new_text: str) -> str:
    """파일 원본과 새 내용을 git 으로 비교해 정확한 unified diff 를 만든다.

    LLM 에게 diff 를 직접 쓰게 하면 @@ 줄 수·문맥·hunk 오프셋을 모두 맞춰야
    하는데, 파일 하나에 hunk 가 다섯 개만 돼도 누적 오프셋에서 틀린다
    (2026-07-20: 문맥은 한 줄도 안 틀렸는데 14 hunk 짜리가 적용 실패).
    내용만 받고 diff 는 git 이 만들면 이 실패가 구조적으로 사라진다.
    """
    orig = repo_root / rel_path
    if not new_text.endswith("\n"):
        new_text += "\n"
    with tempfile.TemporaryDirectory() as td:
        newf = Path(td) / "new"
        newf.write_text(new_text, encoding="utf-8", newline="\n")
        oldf = orig if orig.is_file() else Path(td) / "old"
        if not orig.is_file():
            oldf.write_text("", encoding="utf-8", newline="\n")
        proc = subprocess.run(
            ["git", "diff", "--no-index", "--no-color", "--", str(oldf), str(newf)],
            cwd=repo_root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
    out = proc.stdout
    if not out.strip():
        return ""  # 변경 없음
    body = out.split("\n", 1)[1] if "\n" in out else ""
    lines = [f"diff --git a/{rel_path} b/{rel_path}"]
    for line in body.splitlines():
        if line.startswith("--- "):
            lines.append("--- /dev/null" if not orig.is_file() else f"--- a/{rel_path}")
        elif line.startswith("+++ "):
            lines.append(f"+++ b/{rel_path}")
        elif line.startswith("index ") or line.startswith("new file mode"):
            continue
        else:
            lines.append(line)
    if not orig.is_file():
        lines.insert(1, "new file mode 100644")
    return "\n".join(lines) + "\n"


def implement_and_submit(
    draft: ImproveDraft,
    queue: ProposalQueue,
    brain: str = "opus",
    runner: Callable[[str, str], str] | None = None,
    errors: list[str] | None = None,
    repo_root: Path | None = None,
) -> ActionResult | None:
    """코다리 호출 → diff 추출 → 큐 제출. 실패 시 None.

    실패 사유는 errors 에 남긴다. 예전엔 조용히 None 만 반환해서, CLI 가
    죽은 건지 모델이 diff 펜스를 안 낸 건지 로그만 보고는 구분할 수 없었다.
    """
    errors = errors if errors is not None else []
    run = runner or _run_cli
    try:
        raw = run(brain, build_coder_prompt(draft, repo_root))
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
        errors.append(f"코다리 CLI 실패: {e}"[:300])
        return None
    summary, files = extract_coder_files(raw)
    if not files:
        errors.append(
            f"코다리 응답에 ```file: 펜스 없음 (길이 {len(raw)}). head: {raw[:200]!r}"
        )
        return None
    if repo_root is None:
        errors.append("repo_root 미지정 — diff 를 생성할 수 없다")
        return None
    parts = []
    for rel, body in files:
        try:
            d = make_diff(repo_root, rel, body)
        except (subprocess.SubprocessError, OSError) as e:
            errors.append(f"diff 생성 실패 {rel}: {e}"[:200])
            continue
        if d:
            parts.append(d)
    if not parts:
        errors.append(f"변경 내용 없음 (파일 {len(files)}개 받았으나 원본과 동일)")
        return None
    diff = "".join(parts)
    return queue.submit(
        draft.analysis, draft.diagnosis,
        f"{draft.proposal}\n\n[코다리 구현 요약] {summary}", diff,
        title=draft.title, risk=draft.risk, tradeoff=draft.tradeoff,
    )


# ── 전체 사이클 ───────────────────────────────────────────────────────

def run_improve_cycle(
    perf_summary: str,
    queue: ProposalQueue,
    proposer_brain: str = "codex",
    coder_brain: str = "opus",
    proposer_runner: Callable | None = None,
    coder_runner: Callable | None = None,
    repo_root: Path | None = None,
) -> dict:
    """반환: {"drafts": n, "submitted": [...], "rejected": [...], "failed": n, "errors": [...]}"""
    drafts, errors = propose_improvements(perf_summary, proposer_brain, proposer_runner)
    report = {"drafts": len(drafts), "submitted": [], "rejected": [],
              "failed": 0, "errors": list(errors)}
    for d in drafts:
        r = implement_and_submit(d, queue, coder_brain, coder_runner,
                                 errors=report["errors"], repo_root=repo_root)
        if r is None:
            report["failed"] += 1
        elif r.ok:
            report["submitted"].append(r.card.id)
        else:
            report["rejected"].append(f"{r.card.id}: {r.message}")
    return report
