"""운영 시계 — 미국장 개장 여부 판정. 데이터 품질 가드용 (전략 아님).

미장팀은 일봉 종가 기반이라 장중 타이밍을 쓰지 않는다. 다만 run_daily 를
**미국장이 열려 있는 동안** 돌리면 yfinance 가 미완성(장중) 일봉을 돌려줘
종가 시계열이 오염될 수 있다. 이걸 경고하기 위한 판정만 한다.

한국에서 운영하는 미장 데스크의 올바른 리듬: 미국장 마감(한국시간 대략 새벽
5~6시, DST 로 1시간 이동) 이후 ~ 다음 개장(밤 10:30/11:30) 전. 한국 아침이
자연스러운 시각이다 — 밤새 닫힌 종가를 잡는다 (스윙의 프리마켓과 정반대 이유).
"""
from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")   # DST 자동 반영
OPEN, CLOSE = dtime(9, 30), dtime(16, 0)


def us_eastern_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(ET)


def is_market_open(et: datetime) -> bool:
    """정규장 개장 여부 (평일 09:30~16:00 ET). 휴장일은 고려하지 않는 근사치 —
    휴장일 오탐은 무해하다(경고만 뜬다). 장중 오염 방지가 목적이므로 넉넉히 잡는다."""
    return et.weekday() < 5 and OPEN <= et.time() <= CLOSE


def is_partial_bar(asof, et: datetime) -> bool:
    """마지막 일봉(asof)이 '오늘(ET)' 이고 장중이면 미완성 일봉 위험.

    휴장일엔 오늘 일봉이 아예 없어 asof < 오늘 → 걸리지 않는다 (정밀).
    asof 는 date 객체.
    """
    return is_market_open(et) and asof == et.date()
