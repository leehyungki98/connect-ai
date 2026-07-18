"""브레인 클라이언트: CLI 응답 → 추출 → 스키마 검증 (CLI는 모킹)."""
import json

from autotrader.brain.client import Candidate, ask_brain, build_prompt, extract_json
from autotrader.gates.types import Portfolio, Position
from autotrader.screener.ranking import RankedSymbol

CANDS = [
    Candidate(RankedSymbol("005930", 0.1, 0.08, 0.12, 0.02), 70_000),
    Candidate(RankedSymbol("000660", 0.09, 0.07, 0.11, 0.025), 250_000),
]
PF = Portfolio(10_000_000, 8_000_000, {"035720": Position(5, 600_000)})

GOOD = json.dumps({
    "decisions": [
        {"symbol": "005930", "action": "enter", "entry_price": 70_000,
         "stop_price": 66_000, "target_price": 78_000, "horizon_days": 10,
         "reason": "추세 지속"},
        {"symbol": "000660", "action": "skip", "reason": "과열"},
    ],
    "exits": [{"symbol": "035720", "reason": "모멘텀 소멸"}],
})


def runner_returning(text):
    return lambda brain, prompt: text


def test_plain_json_response_ok():
    r = ask_brain(CANDS, PF, runner=runner_returning(GOOD))
    assert r.ok is True
    assert len(r.output.decisions) == 2 and len(r.output.exits) == 1


def test_fenced_json_with_chatter_ok():
    raw = f"판단 결과입니다.\n```json\n{GOOD}\n```\n이상입니다."
    r = ask_brain(CANDS, PF, runner=runner_returning(raw))
    assert r.ok is True


def test_broken_json_rejected():
    r = ask_brain(CANDS, PF, runner=runner_returning("오늘은 매매하지 않겠습니다."))
    assert r.ok is False and r.output is None


def test_schema_violation_rejected():
    bad = GOOD.replace("70000", "70000.5")
    r = ask_brain(CANDS, PF, runner=runner_returning(bad))
    assert r.ok is False and r.output is None


def test_cli_failure_rejected():
    def boom(brain, prompt):
        raise RuntimeError("claude CLI failed (rc=1)")
    r = ask_brain(CANDS, PF, runner=boom)
    assert r.ok is False and r.output is None
    assert "CLI error" in r.errors[0]


def test_unknown_brain_rejected():
    r = ask_brain(CANDS, PF, brain="gpt5")
    assert r.ok is False and r.output is None


def test_extract_json():
    assert extract_json('x {"a": 1} y') == '{"a": 1}'
    assert extract_json("no braces") == "no braces"


def test_prompt_contains_candidates_and_format():
    p = build_prompt(CANDS, PF)
    assert "005930" in p and "000660" in p and "035720" in p
    assert '"decisions"' in p and "JSON" in p
