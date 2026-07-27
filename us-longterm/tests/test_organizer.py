"""윤가온(정리 담당) — 근거 엮기·환각 차단·서술. 네트워크 없이 가짜 runner."""
import json

from longcore import organizer


def _fake(obj):
    return lambda prompt: "```json\n" + json.dumps(obj) + "\n```"


def _entries():
    return [
        {"impact": 2, "thesis_axis": "성장", "basis": "대형 AI 계약 확보"},
        {"impact": -1, "thesis_axis": "경쟁", "basis": "AMD 신제품 출시로 경쟁 압력"},
        {"impact": -1, "thesis_axis": "경쟁", "basis": "AMD 신제품 출시로 경쟁 압력"},  # 중복
    ]


def test_ticker_block_folds_duplicate_basis():
    b = organizer.ticker_block("NVDA", _entries())
    assert b["score"] == 0                       # 2-1-1
    assert len(b["positives"]) == 1
    assert len(b["negatives"]) == 1              # 중복 근거 접힘


def test_prompt_carries_only_given_tickers():
    p = organizer.build_prompt("엔비디아 어때?", [organizer.ticker_block("NVDA", _entries())])
    assert "NVDA" in p and "TSLA" not in p
    assert "지어내지 마라" in p                    # grounding 규율 존재


def test_synthesize_valid():
    blocks = [organizer.ticker_block("NVDA", _entries())]
    r = organizer.synthesize("어때?", blocks, runner=_fake({
        "summary": "전반적으로 무난",
        "by_ticker": [{"ticker": "NVDA", "mood": "중립",
                       "text": "AI 계약으로 성장은 좋지만 AMD 경쟁이 심해집니다"}]}))
    assert r["summary"] == "전반적으로 무난"
    assert r["by_ticker"][0]["ticker"] == "NVDA"


def test_synthesize_drops_hallucinated_ticker():
    """입력에 없던 종목을 지어내면 버린다 — grounding 의 핵심."""
    blocks = [organizer.ticker_block("NVDA", _entries())]
    r = organizer.synthesize("어때?", blocks, runner=_fake({
        "summary": "s",
        "by_ticker": [
            {"ticker": "NVDA", "mood": "좋음", "text": "좋음"},
            {"ticker": "TSLA", "mood": "좋음", "text": "지어낸 종목"}]}))
    assert [t["ticker"] for t in r["by_ticker"]] == ["NVDA"]


def test_synthesize_drops_empty_text():
    blocks = [organizer.ticker_block("NVDA", _entries())]
    r = organizer.synthesize("q", blocks, runner=_fake({
        "summary": "s", "by_ticker": [{"ticker": "NVDA", "mood": "중립", "text": "  "}]}))
    assert r["by_ticker"] == []


def test_synthesize_bad_mood_defaults_neutral():
    blocks = [organizer.ticker_block("NVDA", _entries())]
    r = organizer.synthesize("q", blocks, runner=_fake({
        "summary": "s", "by_ticker": [{"ticker": "NVDA", "mood": "최고", "text": "x"}]}))
    assert r["by_ticker"][0]["mood"] == "중립"


def test_synthesize_broken_json_empty():
    blocks = [organizer.ticker_block("NVDA", _entries())]
    assert organizer.synthesize("q", blocks, runner=lambda p: "몰라요")["by_ticker"] == []


def test_synthesize_runner_error_empty():
    blocks = [organizer.ticker_block("NVDA", _entries())]
    def boom(_p):
        raise RuntimeError("down")
    assert organizer.synthesize("q", blocks, runner=boom) == {"summary": "", "by_ticker": []}


def test_synthesize_empty_blocks_no_call():
    called = []
    organizer.synthesize("q", [], runner=lambda p: called.append(1) or "{}")
    assert called == []                          # 재료 없으면 LLM 호출 안 함


def test_render_plain_text():
    r = {"summary": "총평", "by_ticker": [
        {"ticker": "NVDA", "mood": "나쁨", "text": "경쟁 심화"}]}
    out = organizer.render(r, asof="2026-07-27")
    assert "윤가온" in out and "NVDA" in out and "경쟁 심화" in out and "🔴" in out


def test_render_empty():
    assert "근거가 없" in organizer.render({"summary": "", "by_ticker": []})
