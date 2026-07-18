"""유니버스 구성: 코스피+코스닥 거래대금 상위 200 (2026-07-19 사용자 확정).

제외: 우선주(종목코드 끝자리 != '0'), 스팩(이름에 '스팩'). ETF/ETN/리츠는
pykrx 주식 티커 목록에 원래 없음. 관리종목 필터는 데이터 소스 한계로 미구현 —
거래정지 종목은 거래대금 0으로 자연 탈락. (후보 단계에서 KIS 조회로 걸러도 됨)

순수 로직(select_universe)과 pykrx I/O(fetch_universe)를 분리 —
전자는 오프라인 테스트, 후자는 실데이터 스모크로 검증.
"""
from __future__ import annotations

import datetime as _dt
import time as _time
from dataclasses import dataclass

from autotrader.screener.ranking import SymbolData

UNIVERSE_SIZE = 200
VALUE_WINDOW_DAYS = 60   # 평균 거래대금 계산 구간
HISTORY_DAYS = 90        # 스크리너에 넘길 종가 이력 (>= 61)
PRELIM_SIZE = 400        # 1차 선별(당일 거래대금) — 전종목 이력 조회 방지
# KRX는 연속 조회를 차단한다(실측: 무지연 시 약 50건째부터 응답 중단).
# 0.25초 간격이면 안정적으로 통과 — 400건 기준 약 2분.
REQUEST_DELAY_SEC = 0.25


@dataclass(frozen=True)
class StockRow:
    symbol: str
    name: str
    avg_value_traded_krw: int  # 최근 60일 평균 거래대금


def select_universe(rows: list[StockRow], size: int = UNIVERSE_SIZE) -> list[str]:
    """결정적 선정: 필터 → 거래대금 내림차순 → 동률 시 코드 오름차순 → 상위 size."""
    eligible = [
        r for r in rows
        if len(r.symbol) == 6 and r.symbol.isdigit()
        and r.symbol.endswith("0")          # 우선주 제외
        and "스팩" not in r.name             # 스팩 제외
        and r.avg_value_traded_krw > 0
    ]
    eligible.sort(key=lambda r: (-r.avg_value_traded_krw, r.symbol))
    return [r.symbol for r in eligible[:size]]


def fetch_universe(asof: str | None = None) -> list[SymbolData]:
    """pykrx로 유니버스 선정 + 종가 이력 수집. (I/O — 실데이터 스모크로 검증)

    asof: "YYYYMMDD" (None이면 최근 거래일)
    """
    from pykrx import stock  # 지연 임포트: 테스트에서 pykrx 불필요

    if asof is None:
        asof = stock.get_nearest_business_day_in_a_week()

    # 종목명 (스팩 필터용) — 종목당 개별 조회(0.6s x 2765 = 28분)를 피하려고
    # 시장 단위 일괄 조회를 쓴다(1초). pykrx가 stock 네임스페이스로 재수출하지
    # 않아 내부 모듈에서 직접 임포트 — 없으면 개별 조회로 자동 폴백.
    names: dict[str, str] = {}
    try:
        from pykrx.website.krx.market.wrap import get_market_ticker_and_name
        for market in ("KOSPI", "KOSDAQ"):
            names.update(get_market_ticker_and_name(asof, market=market).items())
    except ImportError:
        for market in ("KOSPI", "KOSDAQ"):
            for sym in stock.get_market_ticker_list(asof, market=market):
                names[sym] = stock.get_market_ticker_name(sym)

    # 1차 선별: 당일 거래대금 상위 PRELIM_SIZE
    daily = []
    for market in ("KOSPI", "KOSDAQ"):
        df = stock.get_market_ohlcv_by_ticker(asof, market=market)
        for sym, row in df.iterrows():
            daily.append((sym, int(row["거래대금"])))
    daily.sort(key=lambda x: (-x[1], x[0]))
    prelim = [s for s, _ in daily[:PRELIM_SIZE]]

    # 2차: 종목별 이력 조회 → 60일 평균 거래대금 + 90일 종가
    end = _dt.datetime.strptime(asof, "%Y%m%d")
    start_s = (end - _dt.timedelta(days=HISTORY_DAYS * 2)).strftime("%Y%m%d")

    rows: list[StockRow] = []
    history: dict[str, tuple[int, ...]] = {}
    for sym in prelim:
        _time.sleep(REQUEST_DELAY_SEC)   # KRX 연속조회 차단 회피
        df = stock.get_market_ohlcv(start_s, asof, sym)
        if df.empty or len(df) < VALUE_WINDOW_DAYS:
            continue
        df = df.tail(HISTORY_DAYS)
        # pykrx 1.2.8의 단일종목 조회는 '거래대금'을 주지 않는다(시장전체 조회에만 있음).
        # 종가×거래량으로 근사 — 실측 결과 상위200 선정 집합은 동일했다(오차 중앙값 0.03%).
        w = df.tail(VALUE_WINDOW_DAYS)
        if "거래대금" in w.columns:
            avg_value = int(w["거래대금"].mean())
        else:
            avg_value = int((w["종가"].astype("int64") * w["거래량"].astype("int64")).mean())
        rows.append(StockRow(sym, names.get(sym, ""), avg_value))
        history[sym] = tuple(int(x) for x in df["종가"])

    return [SymbolData(s, history[s]) for s in select_universe(rows)]
