"""2단 브레인: GPT 선정자(codex, 공격적) → Claude 판정자(기각 기준 적용).

선정자: 후보를 넉넉히 제안. 출력은 기존 브레인 스키마 그대로 검증(validate_brain_output).
판정자 앞 결정적 프리체크 (코드, LLM 아님 — 같은 입력 같은 출력):
  K1: 손익비 (target-entry)/(entry-stop) < 1.2 → 기각
  K2: 진입가가 현재가에서 ±15% 초과 이탈 → 기각 (비현실적 주문)
  K5: 20일 수익률 +40% 초과 종목 추격 매수 → 기각
판정자(LLM, claude): K3(근거가 제공 데이터와 모순), K4(기존 보유와 사실상 중복 베팅)만 판단.
  기준에 명시적으로 걸릴 때만 기각 — 통과가 기본값. 통과 0개인 날은 정상.
판정자 출력도 결정적 스키마 검증. 무효면 fail-closed: 진입 0건.
청산(exits)은 리스크 축소이므로 판정 없이 통과 (선정자 스키마 검증만).
판정 대상이 0개면 판정자 호출 자체를 생략 (비용 0).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Callable, Sequence

from autotrader.brain.client import Candidate, _run_cli, extract_json
from autotrader.brain.schema import (
    EntryDecision,
    ExitDecision,
    ValidationResult,
    validate_brain_output,
)
from autotrader.gates.types import Portfolio

RR_MIN = 1.2                 # K1 최소 손익비
MAX_ENTRY_DEVIATION = 0.15   # K2 현재가 대비 최대 이탈
MAX_RET20_CHASE = 0.40       # K5 추격 매수 상한


@dataclass(frozen=True)
class Rejection:
    symbol: str
    criterion: str   # K1/K2/K5(결정적) | K3/K4(판정자) | JUDGE_INVALID
    reason: str


@dataclass(frozen=True)
class TwoStageResult:
    ok: bool                              # 선정자 출력 유효 여부
    entries: tuple[EntryDecision, ...]    # 최종 통과 진입
    exits: tuple[ExitDecision, ...]
    rejected: tuple[Rejection, ...]
    errors: tuple[str, ...]


# ── 1단: 선정자 (공격적 제안) ─────────────────────────────────────────

def build_proposer_prompt(candidates: Sequence[Candidate], pf: Portfolio) -> str:
    held = "\n".join(
        f"- {sym}: {p.qty}주, 평가액 {p.value_krw:,}원"
        for sym, p in sorted(pf.positions.items())
    ) or "- 없음"
    cands = "\n".join(
        f"- {c.ranked.symbol}: 종가 {c.last_close:,}원, "
        f"20일수익률 {c.ranked.ret20:+.1%}, 60일수익률 {c.ranked.ret60:+.1%}, "
        f"20일변동성 {c.ranked.vol20:.1%}"
        for c in candidates
    )
    return f"""당신은 한국 주식 스윙 트레이딩 팀의 '선정자'다. 역할: 기회를 넉넉히 제시한다.
뒤에 별도 판정자가 기각 기준으로 거르므로, 근거가 있으면 적극적으로 enter를 제안하라.
단, 근거 없는 enter는 판정자가 기각한다 — 각 제안의 reason에 데이터 근거를 명시하라.

[후보 종목]
{cands}

[현재 보유]
{held}

[계좌] 총평가 {pf.equity_krw:,}원, 현금 {pf.cash_krw:,}원

규칙:
- enter 시 entry_price/stop_price/target_price는 양의 정수(원), 0 < stop < entry < target.
- 손익비((target-entry)/(entry-stop))가 1.2 미만이면 자동 기각되니 그 이상으로 설계하라.
- 진입가는 현재가 ±15% 이내. horizon_days 1~30 정수. reason 500자 이내.
- 후보에 없는 종목 진입 금지, 보유하지 않은 종목 청산 금지.
- 포지션 크기는 시스템이 정한다. 출력하지 마라.

아래 JSON 형식으로만 응답하라. JSON 외 텍스트 금지:
{{"decisions": [{{"symbol": "6자리코드", "action": "enter|skip", "entry_price": 정수, "stop_price": 정수, "target_price": 정수, "horizon_days": 정수, "reason": "근거"}}], "exits": [{{"symbol": "6자리코드", "reason": "근거"}}]}}
skip이면 가격/기간 필드는 null 또는 생략."""


def propose(
    candidates: Sequence[Candidate],
    pf: Portfolio,
    brain: str = "codex",
    runner: Callable[[str, str], str] | None = None,
) -> ValidationResult:
    run = runner or _run_cli
    try:
        raw = run(brain, build_proposer_prompt(candidates, pf))
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
        return ValidationResult(False, (f"proposer CLI error: {e}",), None)
    result = validate_brain_output(
        extract_json(raw),
        candidate_symbols={c.ranked.symbol for c in candidates},
        held_symbols=set(pf.positions.keys()),
    )
    if not result.ok:
        # 원문 스니펫을 남겨 원인 특정 가능하게 (스킬 메뉴/배너/빈 출력 등)
        return ValidationResult(
            False, result.errors + (f"proposer raw head: {raw[:300]!r}",), None
        )
    return result


# ── 결정적 프리체크 (K1/K2/K5) ────────────────────────────────────────

def deterministic_screen(
    enters: Sequence[EntryDecision], candidates: Sequence[Candidate]
) -> tuple[list[EntryDecision], list[Rejection]]:
    by_symbol = {c.ranked.symbol: c for c in candidates}
    remaining, rejected = [], []
    for d in enters:
        c = by_symbol[d.symbol]  # 스키마 검증이 후보 내 존재를 보장
        rr = (d.target_price - d.entry_price) / (d.entry_price - d.stop_price)
        dev = abs(d.entry_price - c.last_close) / c.last_close
        if rr < RR_MIN:
            rejected.append(Rejection(d.symbol, "K1", f"손익비 {rr:.2f} < {RR_MIN}"))
        elif dev > MAX_ENTRY_DEVIATION:
            rejected.append(Rejection(d.symbol, "K2", f"진입가 이탈 {dev:.1%} > {MAX_ENTRY_DEVIATION:.0%}"))
        elif c.ranked.ret20 > MAX_RET20_CHASE:
            rejected.append(Rejection(d.symbol, "K5", f"20일 {c.ranked.ret20:+.1%} 급등 추격"))
        else:
            remaining.append(d)
    return remaining, rejected


# ── 2단: 판정자 ───────────────────────────────────────────────────────

def build_judge_prompt(
    enters: Sequence[EntryDecision], candidates: Sequence[Candidate], pf: Portfolio
) -> str:
    by_symbol = {c.ranked.symbol: c for c in candidates}
    lines = []
    for d in enters:
        c = by_symbol[d.symbol]
        rr = (d.target_price - d.entry_price) / (d.entry_price - d.stop_price)
        lines.append(
            f"- {d.symbol}: enter@{d.entry_price:,} stop@{d.stop_price:,} "
            f"target@{d.target_price:,} (손익비 {rr:.2f}) horizon {d.horizon_days}일\n"
            f"  데이터: 현재가 {c.last_close:,}원, 20일 {c.ranked.ret20:+.1%}, "
            f"60일 {c.ranked.ret60:+.1%}, 변동성 {c.ranked.vol20:.1%}\n"
            f"  선정자 근거: {d.reason}"
        )
    held = ", ".join(sorted(pf.positions.keys())) or "없음"
    symbols = ", ".join(d.symbol for d in enters)
    return f"""당신은 한국 주식 스윙 트레이딩 팀의 '판정자'다. 반박가가 아니다.
아래 기각 기준에 명시적으로 걸리는 제안만 기각하라. 불확실하면 통과가 기본값이다.
통과 0개도 정상이다. 기준 밖의 이유(감, 시황 추측)로 기각하지 마라.

[기각 기준 — 이 두 개만 판단하라. K1/K2/K5는 이미 코드가 걸렀다]
- K3: 선정자의 reason이 함께 제공된 데이터(수익률·변동성·현재가)와 명백히 모순된다.
- K4: 현재 보유 종목({held})과 사실상 같은 베팅의 중복이다.

판정 대상: {symbols}

[제안 목록]
{chr(10).join(lines)}

판정 대상 각 종목에 대해 정확히 하나씩, 아래 JSON 형식으로만 응답하라. JSON 외 텍스트 금지:
{{"verdicts": [{{"symbol": "6자리코드", "verdict": "pass|reject", "criterion": "K3|K4 (pass면 null)", "reason": "근거"}}]}}"""


def validate_judge_output(
    raw: str, expected_symbols: set[str]
) -> tuple[bool, tuple[str, ...], dict[str, tuple[str, str, str]]]:
    """반환: (ok, errors, {symbol: (verdict, criterion, reason)})"""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        return False, (f"invalid JSON: {e}",), {}
    if not isinstance(data, dict) or set(data.keys()) != {"verdicts"}:
        return False, ("root must have exactly 'verdicts'",), {}
    if not isinstance(data["verdicts"], list):
        return False, ("verdicts must be a list",), {}

    errors: list[str] = []
    out: dict[str, tuple[str, str, str]] = {}
    for i, v in enumerate(data["verdicts"]):
        tag = f"verdicts[{i}]"
        if not isinstance(v, dict) or set(v.keys()) != {"symbol", "verdict", "criterion", "reason"}:
            errors.append(f"{tag}: keys must be symbol/verdict/criterion/reason")
            continue
        sym, verdict, crit, reason = v["symbol"], v["verdict"], v["criterion"], v["reason"]
        if sym not in expected_symbols:
            errors.append(f"{tag}: unexpected symbol {sym!r}")
            continue
        if sym in out:
            errors.append(f"{tag}: duplicate symbol {sym}")
            continue
        if not (isinstance(reason, str) and 1 <= len(reason) <= 500):
            errors.append(f"{tag}: bad reason")
            continue
        if verdict == "pass":
            if crit is not None:
                errors.append(f"{tag}: pass must have criterion null")
                continue
            out[sym] = ("pass", "", reason)
        elif verdict == "reject":
            if crit not in ("K3", "K4"):
                errors.append(f"{tag}: reject criterion must be K3|K4, got {crit!r}")
                continue
            out[sym] = ("reject", crit, reason)
        else:
            errors.append(f"{tag}: bad verdict {verdict!r}")
    missing = expected_symbols - set(out.keys())
    if missing and not errors:
        errors.append(f"missing verdicts for {sorted(missing)}")
    if errors:
        return False, tuple(errors), {}
    return True, (), out


# ── 전체 파이프라인 ───────────────────────────────────────────────────

def run_two_stage(
    candidates: Sequence[Candidate],
    pf: Portfolio,
    proposer_brain: str = "codex",
    judge_brain: str = "claude",
    proposer_runner: Callable[[str, str], str] | None = None,
    judge_runner: Callable[[str, str], str] | None = None,
) -> TwoStageResult:
    prop = propose(candidates, pf, brain=proposer_brain, runner=proposer_runner)
    if not prop.ok:
        return TwoStageResult(False, (), (), (), prop.errors)

    enters = [d for d in prop.output.decisions if d.action == "enter"]
    exits = prop.output.exits
    remaining, rejected = deterministic_screen(enters, candidates)

    entries: list[EntryDecision] = []
    errors: tuple[str, ...] = ()
    if remaining:  # 판정 대상 0개면 판정자 호출 생략 (비용 0)
        run = judge_runner or _run_cli
        try:
            raw = run(judge_brain, build_judge_prompt(remaining, candidates, pf))
            ok, errs, verdicts = validate_judge_output(
                extract_json(raw), {d.symbol for d in remaining}
            )
            if not ok:
                errs = errs + (f"judge raw head: {raw[:300]!r}",)
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
            ok, errs, verdicts = False, (f"judge CLI error: {e}",), {}
        if not ok:
            errors = errs
            rejected += [
                Rejection(d.symbol, "JUDGE_INVALID", "판정 출력 무효 — fail-closed")
                for d in remaining
            ]
        else:
            for d in remaining:
                verdict, crit, reason = verdicts[d.symbol]
                if verdict == "pass":
                    entries.append(d)
                else:
                    rejected.append(Rejection(d.symbol, crit, reason))

    return TwoStageResult(True, tuple(entries), exits, tuple(rejected), errors)
