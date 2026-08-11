"""미장팀 사후분석 — 성과 산수·정세 판정·서술. 네트워크 없이 순수 로직."""
from datetime import date

from longcore import review


def test_perf_excess_and_fx_decomp():
    # 데스크 +10%, SPY +4% → 초과 +6%. 환율 -2% 로 원화는 깎인다.
    p = review.perf_summary(
        nav_usd=1100, capital_usd=1000, spy0=100, spy1=104,
        nav_krw=1100 * 1470, capital_krw=1000 * 1500, usdkrw0=1500, usdkrw1=1470)
    assert round(p["r_usd"], 4) == 0.10
    assert round(p["r_spy"], 4) == 0.04
    assert round(p["excess"], 4) == 0.06
    assert p["r_fx"] < 0                       # 환율 하락
    assert p["r_krw"] < p["r_usd"]             # 원화 수익이 달러보다 낮다


def _e(impact, axis="성장", d="2026-07-25"):
    return {"date": d, "source": "n", "event": "x", "impact": impact,
            "thesis_axis": axis, "basis": "b"}


def test_ticker_mood_worst_axis():
    entries = [_e(2, "성장"), _e(2, "성장"), _e(-3 if False else -2, "경쟁"),
               _e(-1, "경쟁")]
    m = review.ticker_mood(entries, date(2026, 7, 26))
    assert m["score"] == 1                      # 4-3
    assert m["worst_axis"][0] == "경쟁" and m["worst_axis"][1] == -3
    assert m["best_axis"][0] == "성장" and m["best_axis"][1] == 4


def test_ticker_mood_empty():
    m = review.ticker_mood([], date(2026, 7, 26))
    assert m["count"] == 0 and m["worst_axis"] is None and m["drop"] is None


def test_narrate_flags_fx_drag_when_usd_up_krw_down():
    """달러론 벌고 원화론 잃을 때 '환율 탓'을 명시해야 한다."""
    perf = review.perf_summary(1100, 1000, 100, 104, 990 * 1000, 1000 * 1100,
                               1100, 990)   # usd +10%, krw 음수 유도
    # 원화 자본 대비 nav_krw 가 낮게: capital_krw=1,100,000, nav_krw=990,000 → -10%
    assert perf["r_usd"] > 0 and perf["r_krw"] < 0
    text = review.narrate("해외증권", "2026-07-25", perf, {}, [], None)
    assert "환율" in text and "실력 아님" in text


def test_narrate_benchmark_win_loss_wording():
    win = review.perf_summary(1100, 1000, 100, 102, 1, 1, 1, 1)
    assert "앞선다" in review.narrate("b", "d", win, {}, [], None)
    loss = review.perf_summary(1010, 1000, 100, 108, 1, 1, 1, 1)
    assert "뒤진다" in review.narrate("b", "d", loss, {}, [], None)


def test_narrate_surfaces_drop_alert():
    perf = review.perf_summary(1000, 1000, 100, 100, 1, 1, 1, 1)
    entries = [_e(-2, "경쟁"), _e(-2, "경쟁"), _e(-1, "성장")]   # 합 -5 → 급락
    moods = {"NVDA": review.ticker_mood(entries, date(2026, 7, 26))}
    text = review.narrate("b", "2026-07-26", perf, moods, [], None)
    assert "🚨" in text and "NVDA" in text and "매도/VOO" in text


def test_narrate_band_and_dday():
    perf = review.perf_summary(1000, 1000, 100, 100, 1, 1, 1, 1)
    t1 = review.narrate("b", "d", perf, {}, ["GROWTH"], 5)
    assert "GROWTH" in t1 and "D-5" in t1
    t2 = review.narrate("b", "d", perf, {}, [], 60)
    assert "목표 범위 안" in t2 and "D-" not in t2       # 창 밖이면 D-day 숨김
