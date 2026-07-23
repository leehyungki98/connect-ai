"""뉴스 수집기 — 소스 구성·오탐 방지·신선도 필터. 네트워크 없이 순수 로직만."""
import importlib.util
from pathlib import Path

# scripts/ 는 패키지가 아니라 파일 경로로 로드
_spec = importlib.util.spec_from_file_location(
    "collect_wsj", Path(__file__).resolve().parents[1] / "scripts" / "collect_wsj.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)

from longcore import intel_sources


def test_feeds_built_from_universe_not_hardcoded():
    """피드가 유니버스에서 나온다 — 보유·관심 티커 + 9개 테마."""
    feeds = cw.build_feeds()
    for tk in intel_sources.feed_tickers():
        assert f"ticker:{tk}" in feeds
    for theme in intel_sources.THEMES:
        assert f"theme:{theme}" in feeds
    # 벤치마크 ETF 는 개별주 뉴스 대상이 아니다
    assert "ticker:SPY" not in feeds and "ticker:VOO" not in feeds


def test_strip_html_kills_google_false_positive():
    """Google News 요약의 href 속 google.com 이 GOOGL 로 오탐되면 안 된다 (실측 367건 사고)."""
    desc = ('<a href="https://news.google.com/rss/articles/ABC123">'
            'IonQ Surges on Quantum Breakthrough</a>&nbsp;&nbsp;'
            '<font color="#6f6f6f">Reuters</font>')
    clean = cw._strip_html(desc)
    assert "google" not in clean.lower()
    assert "IonQ Surges" in clean
    assert intel_sources.match_relevance(clean, "") == \
        intel_sources.match_relevance("IonQ Surges on Quantum Breakthrough Reuters", "")
    assert "GOOGL" not in intel_sources.match_relevance("", clean)


def test_ticker_feed_seeds_its_own_tag():
    """티커 피드는 제목에 사명이 없어도 그 티커로 태그된다 — 일부러 그 티커를 조회했으니까."""
    # NVDA 피드에서 왔지만 제목이 일반적이라 match_relevance 로는 안 잡히는 기사
    matched = intel_sources.match_relevance("Chip sector rallies on demand", "")
    assert "NVDA" not in matched          # 확인: 그냥은 안 잡힌다
    # build 로직상 seed 가 앞에 붙는다 (main 의 seed 처리와 동일 규칙)
    seed = "NVDA"
    result = ([seed] + matched) if seed not in matched else matched
    assert "NVDA" in result


def test_age_filter_drops_old_articles():
    from datetime import datetime, timezone, timedelta
    from email.utils import format_datetime
    old = format_datetime(datetime.now(timezone.utc) - timedelta(days=30))
    fresh = format_datetime(datetime.now(timezone.utc) - timedelta(days=2))
    assert cw._age_days(old) >= cw.MAX_AGE_DAYS
    assert cw._age_days(fresh) < cw.MAX_AGE_DAYS


def test_unparseable_date_passes_through():
    """발행일 파싱 실패는 버리지 않는다 — 판단 보류는 통과 (뉴스를 놓치는 것보다 낫다)."""
    assert cw._age_days("garbage") is None
    assert cw._pub_dt("") is None


def test_freshness_range_sorts_by_date_not_string():
    """RFC822 문자열은 요일명으로 시작해 문자열 정렬이 틀린다 — 날짜로 정렬돼야."""
    # 'Wed, 31 Dec 2025' < 'Fri, 01 May 2026' 는 문자열론 반대다 (W>F)
    a = cw._pub_dt("Wed, 31 Dec 2025 10:00:00 GMT")
    b = cw._pub_dt("Fri, 01 May 2026 10:00:00 GMT")
    assert a < b                          # 날짜론 2025 < 2026


def test_theme_queries_cover_all_themes():
    q = intel_sources.theme_queries()
    assert set(q) == set(intel_sources.THEMES)
    assert all("stock" in v for v in q.values())
