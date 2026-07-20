"""변경 제안 큐 — 유일한 사용자 승인 지점.

카드 = 분석→진단→제안→코드 diff. 사용자는 개별 매매가 아니라 이 카드만 심사한다.
액션: 승인 / 거부 / 수정 지시 후 재제출. 수정 왕복 상한 3회(비용·무한루프 방지).

결정적 안전 규칙 (LLM 아님):
- 편집 금지 구역: 안전층(safety/·gates/·config.py)을 건드리는 diff는 무조건 자동 거부.
  제출 시와 재제출 시 모두 검사한다.
- 승인되어도 diff 적용 → 전체 테스트(게이트 테스트 포함) 통과 시에만 반영 유지.
  실패하면 원상 복구(git apply -R) 후 재제출 요구.
- 지시의 영구 규칙 승격: CLAUDE.md에 append.

카드 상태: pending → approved | rejected | needs_revision(→재제출→pending)
저장: base_dir/pending/*.json, base_dir/history/*.json
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

MAX_REVISIONS = 3

# 리포 루트 기준 상대 경로 접두사 (Opus 코다리의 편집 금지 구역)
FORBIDDEN_ZONES = (
    "trading/autotrader/safety/",
    "trading/autotrader/gates/",
    "trading/autotrader/config.py",
)


@dataclass(frozen=True)
class ProposalCard:
    id: str
    created: str                 # ISO UTC
    status: str                  # pending | approved | rejected | needs_revision
    analysis: str
    diagnosis: str
    proposal: str
    diff: str                    # unified diff (git 형식)
    revisions: tuple[str, ...] = ()   # 수정 지시/실패 사유 이력 (왕복 상한 대상)
    error: str = ""
    # 사용자가 코드를 안 보고 판단하기 위한 필드. 구버전 카드엔 없어 기본값을 둔다.
    title: str = ""
    risk: str = ""
    tradeoff: str = ""


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    card: ProposalCard
    message: str


_DIFF_PATH_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)", re.MULTILINE)


def touched_paths(diff: str) -> set[str]:
    """diff가 건드리는 파일 경로 (결정적 파싱)."""
    out: set[str] = set()
    for a, b in _DIFF_PATH_RE.findall(diff):
        out.add(a)
        out.add(b)
    return out


def forbidden_hit(diff: str, zones: tuple[str, ...]) -> str | None:
    for p in sorted(touched_paths(diff)):
        for z in zones:
            if p == z or p.startswith(z):
                return p
    return None


def _default_test_runner(repo_root: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        ["python3", "-m", "pytest", "tests/", "-q"],
        cwd=repo_root / "trading", capture_output=True, text=True, timeout=600,
        # 테스트 실패 출력에 한국어가 섞인다 — 읽는 쪽도 UTF-8 로 고정.
        encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
    return proc.returncode == 0, tail[0]


class ProposalQueue:
    def __init__(
        self,
        base_dir: Path,
        repo_root: Path,
        forbidden: tuple[str, ...] = FORBIDDEN_ZONES,
        max_revisions: int = MAX_REVISIONS,
        test_runner: Callable[[Path], tuple[bool, str]] | None = None,
    ):
        self._pending = Path(base_dir) / "pending"
        self._history = Path(base_dir) / "history"
        self._repo = Path(repo_root)
        self._forbidden = forbidden
        self._max_rev = max_revisions
        self._run_tests = test_runner or _default_test_runner

    # --- 조회 ---

    def get(self, card_id: str) -> ProposalCard | None:
        for d in (self._pending, self._history):
            f = d / f"{card_id}.json"
            if f.exists():
                raw = json.loads(f.read_text(encoding="utf-8"))
                raw["revisions"] = tuple(raw.get("revisions", ()))
                return ProposalCard(**raw)
        return None

    def list_pending(self) -> list[ProposalCard]:
        cards = []
        if self._pending.exists():
            for f in sorted(self._pending.glob("*.json")):
                raw = json.loads(f.read_text(encoding="utf-8"))
                raw["revisions"] = tuple(raw.get("revisions", ()))
                cards.append(ProposalCard(**raw))
        return cards

    # --- 제출 ---

    def submit(self, analysis: str, diagnosis: str, proposal: str, diff: str,
               title: str = "", risk: str = "", tradeoff: str = "") -> ActionResult:
        now = datetime.now(timezone.utc)
        card = ProposalCard(
            id=now.strftime("%Y%m%d-%H%M%S-%f"),
            created=now.isoformat(),
            status="pending",
            analysis=analysis, diagnosis=diagnosis, proposal=proposal, diff=diff,
            title=title, risk=risk, tradeoff=tradeoff,
        )
        if not all(x.strip() for x in (analysis, diagnosis, proposal, diff)):
            card = replace(card, status="rejected", error="카드 필드 누락 (분석/진단/제안/diff 필수)")
            self._save(card, self._history)
            return ActionResult(False, card, card.error)
        if not touched_paths(diff):
            card = replace(card, status="rejected", error="diff에서 대상 파일을 찾을 수 없음")
            self._save(card, self._history)
            return ActionResult(False, card, card.error)
        hit = forbidden_hit(diff, self._forbidden)
        if hit:
            card = replace(card, status="rejected", error=f"편집 금지 구역: {hit} (안전층은 수정 불가)")
            self._save(card, self._history)
            return ActionResult(False, card, card.error)
        self._save(card, self._pending)
        return ActionResult(True, card, "pending")

    # --- 사용자 액션 ---

    def approve(self, card_id: str) -> ActionResult:
        card = self.get(card_id)
        if card is None or card.status != "pending":
            return ActionResult(False, card, f"pending 카드가 아님: {card_id}")
        hit = forbidden_hit(card.diff, self._forbidden)  # 승인 시 재검사
        if hit:
            return self._finalize(card, "rejected", f"편집 금지 구역: {hit}")

        check = self._git_apply(card.diff, check_only=True)
        if check is not None:
            return self._to_revision(card, f"diff 적용 불가: {check}")
        applied = self._git_apply(card.diff, check_only=False)
        if applied is not None:
            return self._to_revision(card, f"diff 적용 실패: {applied}")

        ok, summary = self._run_tests(self._repo)
        if not ok:
            self._git_apply(card.diff, check_only=False, reverse=True)  # 원상 복구
            return self._to_revision(card, f"테스트 실패로 롤백: {summary}")
        return self._finalize(card, "approved", f"적용 완료, 테스트 통과: {summary}")

    def reject(self, card_id: str, reason: str) -> ActionResult:
        card = self.get(card_id)
        if card is None or card.status != "pending":
            return ActionResult(False, card, f"pending 카드가 아님: {card_id}")
        return self._finalize(card, "rejected", reason)

    def request_revision(self, card_id: str, instruction: str) -> ActionResult:
        card = self.get(card_id)
        if card is None or card.status != "pending":
            return ActionResult(False, card, f"pending 카드가 아님: {card_id}")
        return self._to_revision(card, f"수정 지시: {instruction}")

    def resubmit(self, card_id: str, new_diff: str, note: str = "") -> ActionResult:
        card = self.get(card_id)
        if card is None or card.status != "needs_revision":
            return ActionResult(False, card, f"needs_revision 카드가 아님: {card_id}")
        hit = forbidden_hit(new_diff, self._forbidden)  # 재제출도 동일 검사 (게이트 재통과)
        if hit:
            return self._finalize(card, "rejected", f"편집 금지 구역: {hit}")
        if not touched_paths(new_diff):
            return self._finalize(card, "rejected", "재제출 diff에서 대상 파일을 찾을 수 없음")
        updated = replace(card, diff=new_diff, status="pending",
                          error="", proposal=card.proposal + (f"\n[재제출] {note}" if note else ""))
        self._save(updated, self._pending)
        return ActionResult(True, updated, "pending (재제출)")

    # --- 영구 규칙 승격 ---

    def promote_rule(self, text: str, claude_md: Path | None = None) -> Path:
        target = Path(claude_md) if claude_md else self._repo / "CLAUDE.md"
        header = "## 트레이딩 영구 규칙 (변경 제안 큐에서 승격)"
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        if header not in existing:
            existing = existing.rstrip() + f"\n\n{header}\n" if existing else f"{header}\n"
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        target.write_text(existing.rstrip() + f"\n- [{stamp}] {text.strip()}\n", encoding="utf-8")
        return target

    # --- 내부 ---

    def _to_revision(self, card: ProposalCard, reason: str) -> ActionResult:
        revisions = card.revisions + (reason,)
        if len(revisions) > self._max_rev:
            return self._finalize(
                replace(card, revisions=revisions), "rejected",
                f"수정 왕복 상한({self._max_rev}회) 초과 — 자동 거부",
            )
        updated = replace(card, status="needs_revision", revisions=revisions, error=reason)
        self._save(updated, self._pending)
        return ActionResult(True, updated, reason)

    def _finalize(self, card: ProposalCard, status: str, message: str) -> ActionResult:
        updated = replace(card, status=status, error="" if status == "approved" else message)
        (self._pending / f"{card.id}.json").unlink(missing_ok=True)
        self._save(updated, self._history)
        return ActionResult(status == "approved", updated, message)

    def _save(self, card: ProposalCard, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        f = directory / f"{card.id}.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(card), ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(f)

    def _git_apply(self, diff: str, check_only: bool, reverse: bool = False) -> str | None:
        """성공 시 None, 실패 시 에러 문자열."""
        args = ["git", "apply", "--whitespace=nowarn"]
        if check_only:
            args.append("--check")
        if reverse:
            args.append("--reverse")
        proc = subprocess.run(
            args, input=diff, cwd=self._repo,
            capture_output=True, text=True, timeout=60,
            # diff 에는 한국어 주석과 em dash 가 들어간다. Windows 기본
            # 인코딩(cp949)으로는 못 써서 stdin 쓰기 스레드가 죽고, git 은
            # 입력을 영원히 기다리다 타임아웃난다. 승인 게이트 전체가
            # 이 한 줄 때문에 멎었다.
            encoding="utf-8", errors="replace",
        )
        return None if proc.returncode == 0 else (proc.stderr or "git apply error").strip()[:300]
