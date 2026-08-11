"""정세 자동 채점 — LLM 출력 검증·환각 방지·급락 경보. 네트워크 없이 가짜 runner."""
import json
from datetime import date, timedelta

from longcore import intel, intel_llm


def _fake(scores):
    """scores 리스트를 그대로 뱉는 가짜 LLM runner (코드펜스로 감싸 파싱도 검증)."""
    return lambda prompt: "```json\n" + json.dumps({"scores": scores}) + "\n```"


ART = [{"idx": 0, "title": "Nvidia beats", "summary": "record data center revenue",
        "tickers": ["NVDA"]}]


def test_valid_score_passes():
    r = intel_llm.score_batch(ART, runner=_fake([
        {"idx": 0, "ticker": "NVDA", "impact": 2, "thesis_axis": "성장", "basis": "매출 사상 최대"}]))
    assert len(r) == 1 and r[0]["ticker"] == "NVDA" and r[0]["impact"] == 2


def test_out_of_range_impact_dropped():
    r = intel_llm.score_batch(ART, runner=_fake([
        {"idx": 0, "ticker": "NVDA", "impact": 5, "thesis_axis": "성장", "basis": "x"}]))
    assert r == []                       # -2~+2 밖 → 버림


def test_bad_axis_dropped():
    r = intel_llm.score_batch(ART, runner=_fake([
        {"idx": 0, "ticker": "NVDA", "impact": 1, "thesis_axis": "느낌", "basis": "x"}]))
    assert r == []                       # 고정 6축 밖 → 버림


def test_empty_basis_dropped():
    r = intel_llm.score_batch(ART, runner=_fake([
        {"idx": 0, "ticker": "NVDA", "impact": 1, "thesis_axis": "성장", "basis": "  "}]))
    assert r == []                       # 근거 없는 점수 금지


def test_hallucinated_ticker_dropped():
    """배치에 없던 티커를 지어내면 버린다 — 환각 방지."""
    r = intel_llm.score_batch(ART, runner=_fake([
        {"idx": 0, "ticker": "TSLA", "impact": 2, "thesis_axis": "성장", "basis": "무관"}]))
    assert r == []


def test_unknown_idx_dropped():
    r = intel_llm.score_batch(ART, runner=_fake([
        {"idx": 99, "ticker": "NVDA", "impact": 1, "thesis_axis": "성장", "basis": "x"}]))
    assert r == []


def test_broken_json_yields_empty_not_crash():
    r = intel_llm.score_batch(ART, runner=lambda p: "죄송합니다 응답을 못 만들었어요")
    assert r == []                       # 파싱 실패는 조용히 빈 결과


def test_runner_error_yields_empty():
    def boom(_p):
        raise RuntimeError("CLI down")
    assert intel_llm.score_batch(ART, runner=boom) == []


def test_multi_ticker_article_scored_per_ticker():
    art = [{"idx": 3, "title": "Chip export ban", "summary": "US restricts",
            "tickers": ["NVDA", "TSM"]}]
    r = intel_llm.score_batch(art, runner=_fake([
        {"idx": 3, "ticker": "NVDA", "impact": -2, "thesis_axis": "지정학", "basis": "수출규제 직격"},
        {"idx": 3, "ticker": "TSM", "impact": -1, "thesis_axis": "지정학", "basis": "간접 영향"}]))
    assert {e["ticker"]: e["impact"] for e in r} == {"NVDA": -2, "TSM": -1}


# ── 급락 경보 ──

def _entry(d, impact, axis="성장"):
    return {"date": d, "source": "news", "event": "x",
            "impact": impact, "thesis_axis": axis, "basis": "b"}


def test_recent_drop_fires_below_threshold(tmp_path):
    today = date(2026, 7, 22)
    ds = today.isoformat()
    entries = [_entry(ds, -2), _entry(ds, -2), _entry(ds, -1)]   # 합 -5 ≤ -4
    a = intel.recent_drop(entries, today)
    assert a is not None and a["score"] == -5 and len(a["worst"]) == 3


def test_recent_drop_silent_above_threshold():
    today = date(2026, 7, 22)
    entries = [_entry(today.isoformat(), -1), _entry(today.isoformat(), 2)]  # 합 +1
    assert intel.recent_drop(entries, today) is None


def test_recent_drop_ignores_old_negatives():
    """창 밖(오래된) 악재는 급락으로 안 친다 — 이미 반영된 옛 소식."""
    today = date(2026, 7, 22)
    old = (today - timedelta(days=30)).isoformat()
    entries = [_entry(old, -2), _entry(old, -2), _entry(old, -2)]
    assert intel.recent_drop(entries, today) is None
