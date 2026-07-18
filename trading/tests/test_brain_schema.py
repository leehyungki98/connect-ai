"""목표 3 검증: 잘못된 LLM 출력이 주문으로 안 이어지는지 (output=None 보장)."""
import json

from autotrader.brain.schema import validate_brain_output

CANDS = {"005930", "000660", "035420"}
HELD = {"035720"}


def make(decisions=None, exits=None):
    return json.dumps({"decisions": decisions or [], "exits": exits or []})


ENTER = {
    "symbol": "005930", "action": "enter", "entry_price": 70_000,
    "stop_price": 66_000, "target_price": 78_000, "horizon_days": 10,
    "reason": "돌파 후 눌림",
}


def check_rejected(raw, needle=""):
    r = validate_brain_output(raw, CANDS, HELD)
    assert r.ok is False
    assert r.output is None  # 핵심: 주문으로 이어질 데이터가 없음
    if needle:
        assert any(needle in e for e in r.errors), r.errors


# --- 정상 케이스 ---

def test_valid_output_parses():
    raw = make(
        [ENTER, {"symbol": "000660", "action": "skip", "reason": "과열"}],
        [{"symbol": "035720", "reason": "추세 이탈"}],
    )
    r = validate_brain_output(raw, CANDS, HELD)
    assert r.ok is True and r.errors == ()
    assert len(r.output.decisions) == 2 and len(r.output.exits) == 1
    assert r.output.decisions[0].entry_price == 70_000


def test_empty_lists_ok():
    r = validate_brain_output(make(), CANDS, HELD)
    assert r.ok is True and r.output.decisions == ()


def test_skip_with_null_fields_ok():
    d = {"symbol": "000660", "action": "skip", "reason": "x", "entry_price": None}
    assert validate_brain_output(make([d]), CANDS, HELD).ok is True


# --- 위반 케이스: 전부 거부 + output=None ---

def test_broken_json_rejected():
    check_rejected("{not json", "invalid JSON")


def test_root_not_object_rejected():
    check_rejected("[1, 2]", "root is not an object")


def test_missing_or_extra_top_keys_rejected():
    check_rejected(json.dumps({"decisions": []}), "root keys")
    check_rejected(json.dumps({"decisions": [], "exits": [], "orders": []}), "root keys")


def test_unknown_action_rejected():
    check_rejected(make([{**ENTER, "action": "buy"}]), "bad action")


def test_enter_missing_stop_rejected():
    d = {k: v for k, v in ENTER.items() if k != "stop_price"}
    check_rejected(make([d]), "integers")


def test_stop_entry_target_order_rejected():
    check_rejected(make([{**ENTER, "stop_price": 70_000}]), "require 0 <")   # stop == entry
    check_rejected(make([{**ENTER, "stop_price": 71_000}]), "require 0 <")   # stop > entry
    check_rejected(make([{**ENTER, "target_price": 70_000}]), "require 0 <")  # target == entry
    check_rejected(make([{**ENTER, "target_price": 65_000}]), "require 0 <")  # target < entry


def test_non_integer_prices_rejected():
    check_rejected(make([{**ENTER, "entry_price": 70000.0}]), "integers")   # float
    check_rejected(make([{**ENTER, "entry_price": "70000"}]), "integers")   # str
    check_rejected(make([{**ENTER, "entry_price": True}]), "integers")      # bool
    check_rejected(make([{**ENTER, "entry_price": -70000}]), "require 0 <")


def test_horizon_out_of_range_rejected():
    check_rejected(make([{**ENTER, "horizon_days": 0}]), "horizon_days")
    check_rejected(make([{**ENTER, "horizon_days": 31}]), "horizon_days")


def test_bad_reason_rejected():
    check_rejected(make([{**ENTER, "reason": ""}]), "bad reason")
    check_rejected(make([{**ENTER, "reason": "x" * 501}]), "bad reason")
    check_rejected(make([{**ENTER, "reason": 123}]), "bad reason")


def test_symbol_not_in_candidates_rejected():
    check_rejected(make([{**ENTER, "symbol": "999999"}]), "not in candidates")


def test_bad_symbol_format_rejected():
    check_rejected(make([{**ENTER, "symbol": "5930"}]), "bad symbol")


def test_duplicate_symbols_rejected():
    check_rejected(make([ENTER, dict(ENTER)]), "duplicate")


def test_skip_with_price_rejected():
    d = {"symbol": "000660", "action": "skip", "reason": "x", "entry_price": 1000}
    check_rejected(make([d]), "skip with non-null")


def test_unknown_decision_key_rejected():
    check_rejected(make([{**ENTER, "qty": 100}]), "unknown keys")


def test_decision_not_object_rejected():
    check_rejected(make(["005930"]), "not an object")


def test_exit_not_held_rejected():
    check_rejected(make(exits=[{"symbol": "005930", "reason": "x"}]), "not held")


def test_exit_extra_key_rejected():
    check_rejected(
        make(exits=[{"symbol": "035720", "reason": "x", "price": 1}]),
        "exactly symbol/reason",
    )


def test_one_bad_item_rejects_whole_output():
    """부분 수용 금지: 하나라도 틀리면 전체 거부 (이전 상태 유지)."""
    raw = make([ENTER, {"symbol": "000660", "action": "buy", "reason": "x"}])
    check_rejected(raw)
