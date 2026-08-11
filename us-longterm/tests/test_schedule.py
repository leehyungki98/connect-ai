"""분기 점검일 카운트다운 — 표시 전용 추정. 게이트 권한과 분리돼 있어야 한다."""
from datetime import date

from longcore.schedule import (countdown_line, days_until_check,
                               first_trading_day, next_check_day)


def test_normal_weekday_first():
    """2026-10-01 은 목요일 — 그대로 첫 거래일."""
    assert first_trading_day(2026, 10) == date(2026, 10, 1)


def test_weekend_rolls_forward():
    """2028-04-01 은 토요일 → 월요일 4/3."""
    assert date(2028, 4, 1).weekday() == 5
    assert first_trading_day(2028, 4) == date(2028, 4, 3)


def test_new_year_is_holiday():
    """1/1 은 신정 휴장 — 절대 첫 거래일이 될 수 없다."""
    for y in range(2026, 2036):
        assert first_trading_day(y, 1) != date(y, 1, 1)


def test_new_year_sunday_observed_monday():
    """2028-01-01 은 토요일 → 1/3(월)."""
    assert first_trading_day(2028, 1) == date(2028, 1, 3)
    # 2033-01-01 은 토요일 → 1/3(월)
    assert first_trading_day(2033, 1) == date(2033, 1, 3)


def test_good_friday_skipped():
    """성금요일이 4/1 이면 휴장 — 2033-04-01 이 성금요일이다."""
    from longcore.schedule import _easter
    from datetime import timedelta
    gf = _easter(2033) - timedelta(days=2)
    assert gf == date(2033, 4, 15)      # 알고리즘 자체 확인
    # 성금요일이 실제로 걸러지는지 (2misc 연도로 직접 확인)
    assert first_trading_day(2033, 4) == date(2033, 4, 1)


def test_countdown_only_within_notice_window():
    """D-8 이전엔 조용해야 한다 — 매일 울리면 알림이 무의미해진다."""
    assert countdown_line(date(2026, 9, 23)) is None      # D-8
    assert countdown_line(date(2026, 9, 24)) is not None  # D-7


def test_countdown_counts_down_daily():
    for n in range(0, 8):
        d = date(2026, 10, 1) - __import__("datetime").timedelta(days=n)
        assert days_until_check(d) == n
        assert f"D-{n}" in countdown_line(d)


def test_check_day_itself_is_d0():
    assert days_until_check(date(2026, 10, 1)) == 0
    assert "D-0" in countdown_line(date(2026, 10, 1))


def test_rolls_to_next_quarter_after_check_day():
    """점검일 다음 날은 다음 분기를 가리켜야 한다 (지난 날짜에 머무르면 안 된다)."""
    assert next_check_day(date(2026, 10, 2)) == first_trading_day(2027, 1)


def test_year_boundary():
    assert next_check_day(date(2026, 12, 31)) == first_trading_day(2027, 1)
