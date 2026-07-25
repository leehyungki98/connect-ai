"""미장팀 사후분석(노유진) — 성과 vs SPY + 환율 분해 + 정세 + 비중을 한 데 모아
   **돈의 주인이 읽을 쉬운 말**로 낸다.

경계: 이건 관찰·해석이다. 매매·리밸런싱을 하지 않는다(그쪽은 결정적 코드 + 사람 승인).
      정세 점수도 여기서 '해석'으로만 쓰인다 — DESKS.md 의 정세분석가 출구 ②.

순수 로직만 여기 둔다(성과 산수·정세 판정·서술). 데이터 수집(yfinance·ledger 읽기)은
스크립트가 하고 결과를 넘긴다 — 그래야 네트워크 없이 테스트로 고정된다.
"""
from __future__ import annotations

from .intel import recent_drop, summarize


def perf_summary(nav_usd: float, capital_usd: float, spy0: float, spy1: float,
                 nav_krw: float, capital_krw: float,
                 usdkrw0: float, usdkrw1: float) -> dict:
    """성과 한 줌 — 데스크 수익(USD 판정)·SPY 초과·원화·환율 기여.

    판정은 USD 로 한다(설계 원칙). 원화 수익은 환율이 섞여 실력이 아니라, 따로 분해해
    '달러론 벌었는데 환율에 깎였다' 같은 걸 눈에 보이게 한다.
    """
    r_usd = nav_usd / capital_usd - 1 if capital_usd else 0.0
    r_spy = spy1 / spy0 - 1 if spy0 else 0.0
    r_krw = nav_krw / capital_krw - 1 if capital_krw else 0.0
    r_fx = usdkrw1 / usdkrw0 - 1 if usdkrw0 else 0.0
    return {
        "nav_usd": nav_usd, "nav_krw": nav_krw,
        "r_usd": r_usd, "r_spy": r_spy, "excess": r_usd - r_spy,
        "r_krw": r_krw, "r_fx": r_fx,
    }


def ticker_mood(entries: list, today) -> dict:
    """한 종목의 정세 요약 — 누적점수·축별·급락경보. entries: intel.load_quarter 결과."""
    s = summarize(entries)
    drop = recent_drop(entries, today)
    by_axis = s["by_axis"]
    worst_axis = min(by_axis.items(), key=lambda kv: kv[1]) if by_axis else None
    best_axis = max(by_axis.items(), key=lambda kv: kv[1]) if by_axis else None
    return {
        "count": s["count"], "score": s["score"], "by_axis": by_axis,
        "worst_axis": worst_axis, "best_axis": best_axis, "drop": drop,
    }


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def narrate(bucket: str, asof: str, perf: dict, moods: dict,
            band_breached: list, dday: int | None) -> str:
    """쉬운 말 서술 — 전문용어·영어약어 금지. 돈의 주인이 코드 몰라도 읽힌다.

    moods: {ticker: ticker_mood(...) 결과}. band_breached: 밴드 벗어난 슬리브 목록.
    dday: 다음 분기 점검일까지 남은 일수(없으면 None).
    """
    L = []
    L.append(f"📊 미장팀 사후분석 · {asof} 기준")
    L.append("")

    # ── 지금 얼마 ──
    L.append(f"💰 평가금액  ₩{perf['nav_krw']:,.0f}  (${perf['nav_usd']:,.0f})")

    # ── 시장 대비 ──
    exc = perf["excess"]
    vs = "앞선다" if exc > 0.001 else ("뒤진다" if exc < -0.001 else "비슷하다")
    L.append(f"📈 시장(S&P500)보다 {vs} — 우리 {_pct(perf['r_usd'])} vs 시장 "
             f"{_pct(perf['r_spy'])} (차이 {_pct(exc)})")

    # ── 환율 분해: 원화가 달러와 갈릴 때만 짚는다 ──
    if abs(perf["r_fx"]) > 0.003:
        if perf["r_usd"] > 0 > perf["r_krw"]:
            L.append(f"💱 달러로는 벌었지만 환율이 {_pct(perf['r_fx'])} 움직여 "
                     f"원화로는 {_pct(perf['r_krw'])}가 됐다 (환율 탓, 실력 아님)")
        elif perf["r_usd"] < 0 < perf["r_krw"]:
            L.append(f"💱 달러로는 빠졌지만 환율이 {_pct(perf['r_fx'])} 도와 "
                     f"원화로는 {_pct(perf['r_krw'])}로 버텼다")
        else:
            L.append(f"💱 환율이 {_pct(perf['r_fx'])} 움직여 원화 수익은 "
                     f"{_pct(perf['r_krw'])}")

    # ── 종목별 분위기 ──
    if moods:
        L.append("")
        L.append("🏢 보유 종목 분위기 (뉴스 기반)")
        AX = {"수익성": "이익", "성장": "성장", "밸류": "밸류에이션",
              "지정학": "지정학", "규제": "규제", "경쟁": "경쟁"}
        for tk, m in moods.items():
            if m["count"] == 0:
                L.append(f"  · {tk} — 이번 분기 눈에 띄는 소식 없음")
                continue
            tone = "좋음" if m["score"] > 1 else ("나쁨" if m["score"] < -1 else "중립")
            line = f"  · {tk} — 분위기 {tone} (점수 {m['score']:+d}, 뉴스 {m['count']}건)"
            # 가장 나쁜 축을 한 마디 — 뭘 조심할지
            if m["worst_axis"] and m["worst_axis"][1] < 0:
                ax, v = m["worst_axis"]
                line += f", 특히 {AX.get(ax, ax)} 쪽이 약함({v:+d})"
            L.append(line)
            if m["drop"]:
                d = m["drop"]
                L.append(f"     🚨 최근 {d['window_days']}일 급락 (점수 {d['score']}) "
                         f"— 매도/VOO 검토 대상")

    # ── 비중·리밸런싱 ──
    L.append("")
    if band_breached:
        L.append(f"⚖️ 목표 비중에서 벗어난 항목: {', '.join(band_breached)} "
                 f"— 분기 점검일에 조정 검토")
    else:
        L.append("⚖️ 비중은 목표 범위 안 — 손댈 것 없음")
    if dday is not None and dday <= 7:
        L.append(f"🔔 분기 점검일 D-{dday} — 리밸런싱 준비 기간")

    return "\n".join(L)
