"""운영 시계 — 미국장 개장 판정 + 미완성 일봉 위험 감지 (순수 함수, tz 고정)."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from longcore import clock

ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def test_market_open_midday_weekday():
    assert clock.is_market_open(_et(2026, 7, 20, 12, 0))      # 월요일 정오


def test_market_closed_before_open():
    assert not clock.is_market_open(_et(2026, 7, 20, 9, 0))    # 09:00 < 09:30


def test_market_closed_after_close():
    assert not clock.is_market_open(_et(2026, 7, 20, 16, 1))   # 16:01 > 16:00


def test_market_closed_weekend():
    assert not clock.is_market_open(_et(2026, 7, 18, 12, 0))   # 토요일


def test_open_boundary_inclusive():
    assert clock.is_market_open(_et(2026, 7, 20, 9, 30))       # 정확히 개장
    assert clock.is_market_open(_et(2026, 7, 20, 16, 0))       # 정확히 마감시각


def test_partial_bar_when_today_and_open():
    """오늘 일봉 + 장중 → 미완성 위험."""
    et = _et(2026, 7, 20, 12, 0)
    assert clock.is_partial_bar(date(2026, 7, 20), et)


def test_no_partial_when_bar_is_prior_session():
    """마지막 일봉이 어제면(오늘 아직 개장 전·마감 후) 완성된 종가 → 안전."""
    et = _et(2026, 7, 20, 12, 0)
    assert not clock.is_partial_bar(date(2026, 7, 17), et)     # 금요일 종가


def test_no_partial_when_market_closed_even_if_today():
    """장 마감 후엔 오늘 일봉이라도 완성된 종가."""
    et = _et(2026, 7, 20, 20, 0)      # 20:00 ET, 마감 후
    assert not clock.is_partial_bar(date(2026, 7, 20), et)


def test_holiday_not_flagged():
    """휴장일엔 오늘 일봉이 아예 없어 asof<오늘 → 오탐 안 남 (근사치라도 정밀)."""
    et = _et(2026, 7, 3, 12, 0)       # 가상 휴장 평일, 장중 시각이지만
    assert not clock.is_partial_bar(date(2026, 7, 2), et)      # 마지막 일봉은 전날
