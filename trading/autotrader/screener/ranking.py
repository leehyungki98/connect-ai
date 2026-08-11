"""결정적 스크리너: 유니버스(~200종목) → 상위 5~15개 랭킹.

절대규칙 2 준수: 순수 함수, 같은 입력이면 항상 같은 출력. LLM 호출 금지.

기준 (2026-07-19 사용자 확정: 모멘텀+추세 복합):
  필터 (하나라도 걸리면 제외):
    - 이력 61 거래일 미만 또는 가격 <= 0
    - 현재가 <= 20일 이동평균 (추세 필터)
    - 20일 일수익률 모표준편차 > 4% (변동성 상한)
  점수: score = 0.6 * 20일수익률 + 0.4 * 60일수익률
  정렬: score 내림차순, 동점이면 종목코드 오름차순 (재현성 보장)
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Sequence

MIN_HISTORY = 61          # 60일 수익률 계산에 필요한 최소 종가 수
VOL20_CAP = 0.04          # 20일 일수익률 표준편차 상한
W20, W60 = 0.6, 0.4       # 모멘텀 가중치


@dataclass(frozen=True)
class SymbolData:
    symbol: str
    closes: tuple[int, ...]  # 일봉 종가(원), 과거 → 최근 순


@dataclass(frozen=True)
class RankedSymbol:
    symbol: str
    score: float
    ret20: float
    ret60: float
    vol20: float


def rank(universe: Sequence[SymbolData], top_n: int = 10) -> list[RankedSymbol]:
    if not 5 <= top_n <= 15:
        raise ValueError(f"top_n must be in [5, 15], got {top_n}")
    scored = [r for s in universe if (r := _score(s)) is not None]
    scored.sort(key=lambda r: (-r.score, r.symbol))
    return scored[:top_n]


def _score(s: SymbolData) -> RankedSymbol | None:
    c = s.closes
    if len(c) < MIN_HISTORY:
        return None
    window = c[-MIN_HISTORY:]
    if any(x <= 0 for x in window):
        return None

    last = c[-1]
    ma20 = sum(c[-20:]) / 20
    if last <= ma20:
        return None

    daily_rets = [c[-i] / c[-i - 1] - 1 for i in range(1, 21)]
    vol20 = statistics.pstdev(daily_rets)
    if vol20 > VOL20_CAP:
        return None

    ret20 = last / c[-21] - 1
    ret60 = last / c[-61] - 1
    return RankedSymbol(s.symbol, W20 * ret20 + W60 * ret60, ret20, ret60, vol20)
