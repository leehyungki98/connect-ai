"""다음 분기 점검일 카운트다운 (표시 전용).

⚠ 이건 **추정치**다. 체결을 허가하는 권한은 gates/rebalance_gate.py 뿐이며, 그쪽은
실제 시세 인덱스(trading_days)로 '그 달의 첫 거래일'을 확정한다. 여기서는 미래 날짜라
그 인덱스가 없어 달력으로 근사한다 — 주말·신정·성금요일만 반영한다.

둘을 섞으면 안 되는 이유: 알림이 D-0 이라고 해서 게이트가 열리는 게 아니고,
게이트가 닫혀 있다고 알림이 틀린 것도 아니다. 알림은 '슬슬 준비해라'는 신호일 뿐이다.
"""
from datetime import date, timedelta

QUARTER_MONTHS = (1, 4, 7, 10)
NOTICE_DAYS = 7           # D-7 부터 알린다


def _easter(year: int) -> date:
    """부활절 (그레고리력 익명 알고리즘). 성금요일 = 부활절 − 2일."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f, g = (b + 8) // 25, (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _is_closed(d: date) -> bool:
    """미국장 휴장 여부 — 분기 첫날에 걸릴 수 있는 것만.

    1·4·7·10월 1일 근처에 실제로 겹칠 수 있는 건 주말·신정·성금요일뿐이다.
    (독립기념일 7/4, 추수감사절 등은 그 달 '첫 거래일'과 겹치지 않는다.)
    """
    if d.weekday() >= 5:                     # 토·일
        return True
    if d.month == 1 and d.day == 1:          # 신정
        return True
    if d.month == 1 and d.day == 2 and date(d.year, 1, 1).weekday() == 6:
        return True                          # 1/1 이 일요일 → 월요일 대체휴일
    if d == _easter(d.year) - timedelta(days=2):   # 성금요일
        return True
    return False


def first_trading_day(year: int, month: int) -> date:
    """그 달의 첫 거래일(추정)."""
    d = date(year, month, 1)
    while _is_closed(d):
        d += timedelta(days=1)
    return d


def next_check_day(today: date) -> date:
    """오늘 이후(당일 포함) 가장 가까운 분기 점검일."""
    for year in (today.year, today.year + 1):
        for month in QUARTER_MONTHS:
            d = first_trading_day(year, month)
            if d >= today:
                return d
    raise AssertionError("unreachable")      # 위 루프가 항상 하나를 찾는다


def days_until_check(today: date) -> int:
    """다음 분기 점검일까지 남은 **달력일**. 오늘이 점검일이면 0."""
    return (next_check_day(today) - today).days


def countdown_line(today: date) -> str | None:
    """D-7 이내일 때만 한 줄. 아니면 None (조용히 넘어간다)."""
    n = days_until_check(today)
    if n > NOTICE_DAYS:
        return None
    d = next_check_day(today)
    if n == 0:
        return f"🔔 리밸런싱 D-0 — 오늘이 분기 점검일이다 ({d})"
    return f"🔔 리밸런싱 D-{n} — {d} 분기 점검일 (남은 {n}일)"
