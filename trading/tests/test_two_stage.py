"""목표 5 검증: 통과 경로 존재 + 후보 0개 정상 처리 + 결정적 기각 기준."""
import json

from autotrader.brain.client import Candidate
from autotrader.brain.two_stage import (
    deterministic_screen,
    run_two_stage,
    validate_judge_output,
)
from autotrader.gates.types import Portfolio, Position
from autotrader.screener.ranking import RankedSymbol

CANDS = [
    Candidate(RankedSymbol("005930", 0.10, 0.08, 0.12, 0.02), 70_000),
    Candidate(RankedSymbol("000660", 0.09, 0.07, 0.11, 0.02), 250_000),
]
PF = Portfolio(10_000_000, 8_000_000, {"035720": Position(5, 600_000)})


def enter(symbol="005930", entry=70_000, stop=66_000, target=78_000):
    # 손익비 = 8000/4000 = 2.0
    return {"symbol": symbol, "action": "enter", "entry_price": entry,
            "stop_price": stop, "target_price": target, "horizon_days": 10,
            "reason": "상승 추세 지속"}


def proposer(decisions, exits=None):
    payload = json.dumps({"decisions": decisions, "exits": exits or []})
    return lambda brain, prompt: payload


def judge(verdict_map):
    """verdict_map: {symbol: ("pass", None, reason) | ("reject", "K3", reason)}"""
    def run(brain, prompt):
        return json.dumps({"verdicts": [
            {"symbol": s, "verdict": v, "criterion": c, "reason": r}
            for s, (v, c, r) in verdict_map.items()
        ]})
    return run


def judge_never_called(brain, prompt):
    raise AssertionError("판정자가 호출되면 안 되는 케이스")


# --- 통과 경로 존재 (핵심 검증 1) ---

def test_pass_path_exists():
    r = run_two_stage(
        CANDS, PF,
        proposer_runner=proposer([enter("005930"), enter("000660", 250_000, 240_000, 265_000)]),
        judge_runner=judge({
            "005930": ("pass", None, "기준 미해당"),
            "000660": ("reject", "K4", "보유 종목과 중복 베팅"),
        }),
    )
    assert r.ok is True
    assert [d.symbol for d in r.entries] == ["005930"]  # 통과 경로 존재
    assert r.rejected[0].symbol == "000660" and r.rejected[0].criterion == "K4"


def test_pass_is_default_all_pass():
    r = run_two_stage(
        CANDS, PF,
        proposer_runner=proposer([enter("005930")]),
        judge_runner=judge({"005930": ("pass", None, "통과")}),
    )
    assert len(r.entries) == 1 and r.rejected == () and r.errors == ()


# --- 후보 0개 정상 처리 (핵심 검증 2) ---

def test_zero_proposals_is_normal_and_judge_not_called():
    r = run_two_stage(
        CANDS, PF,
        proposer_runner=proposer([{"symbol": "005930", "action": "skip", "reason": "관망"}]),
        judge_runner=judge_never_called,
    )
    assert r.ok is True and r.entries == () and r.errors == ()


def test_all_deterministically_rejected_skips_judge():
    bad_rr = enter("005930", 70_000, 66_000, 72_000)  # 손익비 0.5
    r = run_two_stage(CANDS, PF, proposer_runner=proposer([bad_rr]),
                      judge_runner=judge_never_called)
    assert r.ok is True and r.entries == ()
    assert r.rejected[0].criterion == "K1"


# --- 결정적 프리체크 K1/K2/K5 ---

def make_entry_decision(d):
    from autotrader.brain.schema import EntryDecision
    return EntryDecision(d["symbol"], "enter", d["entry_price"], d["stop_price"],
                         d["target_price"], d["horizon_days"], d["reason"])


def test_k1_low_rr_rejected():
    remaining, rej = deterministic_screen(
        [make_entry_decision(enter("005930", 70_000, 66_000, 74_000))], CANDS
    )  # 손익비 1.0
    assert remaining == [] and rej[0].criterion == "K1"


def test_k1_boundary_1_2_passes():
    remaining, rej = deterministic_screen(
        [make_entry_decision(enter("005930", 70_000, 65_000, 76_000))], CANDS
    )  # 손익비 = 6000/5000 = 1.2 — 경계는 통과
    assert len(remaining) == 1 and rej == []


def test_k2_entry_far_from_price_rejected():
    remaining, rej = deterministic_screen(
        [make_entry_decision(enter("005930", 85_000, 80_000, 95_000))], CANDS
    )  # 이탈 21.4%
    assert rej[0].criterion == "K2"


def test_k5_chasing_spike_rejected():
    hot = [Candidate(RankedSymbol("005930", 0.5, 0.45, 0.6, 0.03), 70_000)]
    remaining, rej = deterministic_screen(
        [make_entry_decision(enter("005930"))], hot
    )  # ret20 +45%
    assert rej[0].criterion == "K5"


# --- 판정자 출력 무효 → fail-closed ---

def test_invalid_judge_output_fails_closed():
    r = run_two_stage(
        CANDS, PF,
        proposer_runner=proposer([enter("005930")]),
        judge_runner=lambda b, p: "판정 결과: 통과입니다",  # JSON 아님
    )
    assert r.ok is True and r.entries == ()
    assert r.rejected[0].criterion == "JUDGE_INVALID" and r.errors != ()


def test_judge_missing_symbol_invalid():
    ok, errs, _ = validate_judge_output(
        json.dumps({"verdicts": []}), {"005930"}
    )
    assert ok is False and "missing" in errs[0]


def test_judge_bad_criterion_invalid():
    ok, errs, _ = validate_judge_output(
        json.dumps({"verdicts": [{"symbol": "005930", "verdict": "reject",
                                  "criterion": "K9", "reason": "x"}]}),
        {"005930"},
    )
    assert ok is False


def test_judge_pass_with_criterion_invalid():
    ok, errs, _ = validate_judge_output(
        json.dumps({"verdicts": [{"symbol": "005930", "verdict": "pass",
                                  "criterion": "K3", "reason": "x"}]}),
        {"005930"},
    )
    assert ok is False


# --- 선정자 실패 / 청산 통과 ---

def test_proposer_failure_blocks_everything():
    r = run_two_stage(CANDS, PF, proposer_runner=lambda b, p: "{broken",
                      judge_runner=judge_never_called)
    assert r.ok is False and r.entries == () and r.exits == ()


def test_exits_pass_through_without_judge():
    r = run_two_stage(
        CANDS, PF,
        proposer_runner=proposer([], exits=[{"symbol": "035720", "reason": "추세 이탈"}]),
        judge_runner=judge_never_called,
    )
    assert r.ok is True and r.exits[0].symbol == "035720"
