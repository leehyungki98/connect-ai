"""LLM 브레인 출력 스키마 + 결정적 검증기.

절대규칙 3: 이 검증을 통과한 출력만 게이트로 넘어간다.
검증 실패 시 output=None — 주문으로 이어질 데이터 자체가 없다(이전 상태 유지).

기대 JSON (LLM은 포지션 크기를 정하지 않는다 — 수량은 별도 결정적 계산):
{
  "decisions": [
    {"symbol": "005930", "action": "enter", "entry_price": 70000,
     "stop_price": 66000, "target_price": 78000, "horizon_days": 10,
     "reason": "..."},
    {"symbol": "000660", "action": "skip", "reason": "..."}
  ],
  "exits": [
    {"symbol": "035420", "reason": "..."}    # 보유 종목 청산 지시
  ]
}

규칙:
- 최상위 키는 decisions/exits 둘 다 필수, 그 외 키 금지.
- 가격은 양의 정수(원). bool/float/문자열 거부. 0 < stop < entry < target.
- horizon_days: 정수 1~30. reason: 문자열 1~500자.
- decisions의 symbol은 후보 목록 안에서만, exits는 보유 종목 안에서만. 중복 금지.
- skip이면 가격/기간 필드는 없거나 null이어야 함.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

_SYMBOL_RE = re.compile(r"^\d{6}$")
MAX_REASON_LEN = 500
MAX_HORIZON_DAYS = 30

_ENTER_FIELDS = ("entry_price", "stop_price", "target_price", "horizon_days")
_DECISION_KEYS = {"symbol", "action", "reason", *_ENTER_FIELDS}


@dataclass(frozen=True)
class EntryDecision:
    symbol: str
    action: str                    # "enter" | "skip"
    entry_price: int | None
    stop_price: int | None
    target_price: int | None
    horizon_days: int | None
    reason: str


@dataclass(frozen=True)
class ExitDecision:
    symbol: str
    reason: str


@dataclass(frozen=True)
class BrainOutput:
    decisions: tuple[EntryDecision, ...]
    exits: tuple[ExitDecision, ...]


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: tuple[str, ...]
    output: BrainOutput | None


def _is_int(x) -> bool:
    return type(x) is int  # bool은 int의 서브클래스이므로 type()으로 배제


def _valid_reason(x) -> bool:
    return isinstance(x, str) and 1 <= len(x) <= MAX_REASON_LEN


def validate_brain_output(
    raw: str,
    candidate_symbols: set[str],
    held_symbols: set[str],
) -> ValidationResult:
    errors: list[str] = []

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        return ValidationResult(False, (f"invalid JSON: {e}",), None)

    if not isinstance(data, dict):
        return ValidationResult(False, ("root is not an object",), None)
    if set(data.keys()) != {"decisions", "exits"}:
        return ValidationResult(
            False, (f"root keys must be decisions/exits, got {sorted(data.keys())}",), None
        )
    if not isinstance(data["decisions"], list) or not isinstance(data["exits"], list):
        return ValidationResult(False, ("decisions/exits must be lists",), None)

    decisions: list[EntryDecision] = []
    seen: set[str] = set()
    for i, d in enumerate(data["decisions"]):
        tag = f"decisions[{i}]"
        if not isinstance(d, dict):
            errors.append(f"{tag}: not an object")
            continue
        if not set(d.keys()) <= _DECISION_KEYS:
            errors.append(f"{tag}: unknown keys {sorted(set(d.keys()) - _DECISION_KEYS)}")
            continue
        sym = d.get("symbol")
        if not (isinstance(sym, str) and _SYMBOL_RE.match(sym)):
            errors.append(f"{tag}: bad symbol {sym!r}")
            continue
        if sym not in candidate_symbols:
            errors.append(f"{tag}: {sym} not in candidates")
            continue
        if sym in seen:
            errors.append(f"{tag}: duplicate symbol {sym}")
            continue
        seen.add(sym)
        action = d.get("action")
        if not _valid_reason(d.get("reason")):
            errors.append(f"{tag}: bad reason")
            continue

        if action == "skip":
            bad = [f for f in _ENTER_FIELDS if d.get(f) is not None]
            if bad:
                errors.append(f"{tag}: skip with non-null fields {bad}")
                continue
            decisions.append(EntryDecision(sym, "skip", None, None, None, None, d["reason"]))
        elif action == "enter":
            if not all(_is_int(d.get(f)) for f in _ENTER_FIELDS):
                errors.append(f"{tag}: enter fields must all be integers")
                continue
            entry, stop, target, horizon = (d[f] for f in _ENTER_FIELDS)
            if not 0 < stop < entry < target:
                errors.append(
                    f"{tag}: require 0 < stop({stop}) < entry({entry}) < target({target})"
                )
                continue
            if not 1 <= horizon <= MAX_HORIZON_DAYS:
                errors.append(f"{tag}: horizon_days {horizon} out of [1, {MAX_HORIZON_DAYS}]")
                continue
            decisions.append(EntryDecision(sym, "enter", entry, stop, target, horizon, d["reason"]))
        else:
            errors.append(f"{tag}: bad action {action!r}")

    exits: list[ExitDecision] = []
    seen_exit: set[str] = set()
    for i, e in enumerate(data["exits"]):
        tag = f"exits[{i}]"
        if not isinstance(e, dict) or set(e.keys()) != {"symbol", "reason"}:
            errors.append(f"{tag}: must have exactly symbol/reason")
            continue
        sym = e["symbol"]
        if not (isinstance(sym, str) and _SYMBOL_RE.match(sym)):
            errors.append(f"{tag}: bad symbol {sym!r}")
            continue
        if sym not in held_symbols:
            errors.append(f"{tag}: {sym} not held")
            continue
        if sym in seen_exit:
            errors.append(f"{tag}: duplicate symbol {sym}")
            continue
        seen_exit.add(sym)
        if not _valid_reason(e["reason"]):
            errors.append(f"{tag}: bad reason")
            continue
        exits.append(ExitDecision(sym, e["reason"]))

    if errors:
        return ValidationResult(False, tuple(errors), None)
    return ValidationResult(True, (), BrainOutput(tuple(decisions), tuple(exits)))
