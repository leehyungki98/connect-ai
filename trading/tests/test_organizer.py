"""윤가온 스윙 두뇌 — 범용 엮기·근거 규율·서술. 네트워크 없이 가짜 runner."""
import json

from autotrader import organizer


def _fake(obj):
    return lambda prompt: "```json\n" + json.dumps(obj) + "\n```"


BLOCKS = [
    {"source": "현재 상태", "facts": ["시장 폭 30%로 기준 밑이라 관망 중"]},
    {"source": "현빈 사후분석", "facts": ["오늘 매매 없음", "안전장치 이상 없음"]},
]


def test_synthesize_valid():
    r = organizer.synthesize("스윙 어때?", BLOCKS,
                             runner=_fake({"answer": "약한 시장이라 쉬는 중입니다."}))
    assert "쉬는 중" in r["answer"]


def test_prompt_carries_material_and_rule():
    p = organizer.build_prompt("스윙 어때?", BLOCKS)
    assert "시장 폭 30%" in p and "지어내지 마라" in p and "쉬운 말" in p


def test_empty_blocks_no_call():
    called = []
    r = organizer.synthesize("q", [], runner=lambda p: called.append(1) or "{}")
    assert called == [] and r["answer"] == ""


def test_blocks_with_no_facts_skipped():
    called = []
    organizer.synthesize("q", [{"source": "x", "facts": []}],
                         runner=lambda p: called.append(1) or "{}")
    assert called == []                       # 사실 없는 블록뿐이면 호출 안 함


def test_broken_json_empty_not_crash():
    r = organizer.synthesize("q", BLOCKS, runner=lambda p: "몰라요 답 못만듦")
    assert r["answer"] == ""


def test_runner_error_empty():
    def boom(_p):
        raise RuntimeError("cli down")
    assert organizer.synthesize("q", BLOCKS, runner=boom)["answer"] == ""


def test_render_plain():
    out = organizer.render({"answer": "약한 시장이라 관망 중"}, asof="2026-07-27")
    assert "윤가온" in out and "관망 중" in out


def test_render_empty():
    assert "근거가 부족" in organizer.render({"answer": "  "})
