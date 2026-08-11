"""뉴스 수집 — 관련도 필터링 후 staging에 적재.

실행: python scripts/collect_wsj.py

로직:
  1. 살아있는 피드에서 item 파싱 (title, link, pubDate, description)
     · Yahoo Finance 티커별 — 보유·관심 종목 뉴스를 깊게 (feed_tickers)
     · Google News 테마 검색 — 신흥 테마 판을 키워드로 (theme_queries)
  2. match_relevance 로 필터 (관련 티커/매크로만)
  3. 기존 seen_urls + state/intel_staging/ jsonl 에서 중복 제거
  4. 신규만 state/intel_staging/{분기}.jsonl 에 append
  5. seen_urls.txt 갱신

점수는 절대 매기지 않는다 — status="pending" 만.

⚠ 원래 WSJ 공개 RSS(feeds.a.dj.com)를 썼으나 2025-01 에 갱신이 멈춰(HTTP 200 이지만
   내용 동결) 1년 반 묵은 기사만 나왔다 (2026-07-24 확인·교체). 라이브 소스는
   HTTP 200 만으로는 신선도를 보장 못 한다 — 그래서 이 스크립트는 수집분의 발행일
   범위를 항상 출력해 동결을 눈으로 잡을 수 있게 한다.
"""
import json
import sys
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

# 모듈 경로 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longcore import intel_sources
from longcore.store import STATE_DIR
from longcore.intel import quarter_of


# 일일 수집이므로 이보다 오래된 기사는 버린다. Yahoo 티커 피드는 대부분 당일치라
# 영향이 없고, Google News 검색이 관련도순으로 수개월치를 섞어 보내는 걸 잘라낸다.
# 첫 실행은 최근 14일치를 씨앗으로 담고, 이후 매일 신규분만 얹힌다.
MAX_AGE_DAYS = 14


def _pub_dt(pubdate: str):
    """RFC822 pubDate → tz-aware datetime. 파싱 실패는 None."""
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(pubdate)
        if dt is None:
            return None
        from datetime import timezone
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _age_days(pubdate: str):
    """오늘로부터 며칠 전 기사인가. 파싱 불가면 None (판단 보류 → 통과시킨다)."""
    from datetime import datetime, timezone
    dt = _pub_dt(pubdate)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).days


def _freshness_warn(latest_pubdate: str) -> None:
    """가장 최근 기사가 오늘로부터 너무 오래됐으면 경고. 소스 동결 조기경보.

    RFC822 형식(예 'Thu, 23 Jul 2026 19:17:00 GMT')을 파싱한다. 파싱 실패는
    조용히 넘긴다 — 신선도 경고는 보조 장치라 여기서 죽으면 안 된다.
    """
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(latest_pubdate)
        if dt is None:
            return
        age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).days
        if age > 7:
            print(f"  ⚠ 경고: 가장 최근 기사가 {age}일 전이다 — 소스가 동결됐을 수 있다 "
                  f"(WSJ 처럼 HTTP 는 되는데 내용이 묵는 경우).")
    except (TypeError, ValueError):
        pass


def _strip_html(text: str) -> str:
    """HTML 태그·엔티티 제거 → 순수 텍스트. Google News 요약이 필수다.

    Google News RSS 요약은 <a href="...news.google.com/..."> 로 감싸여 있어, 안 걷으면
    href 속 'google.com' 때문에 모든 기사가 GOOGL 로 오탐된다(실측 367건). 태그를
    걷으면 href 속성이 통째로 사라지고 기사 제목·출처 텍스트만 남는다.
    """
    import html
    import re
    no_tags = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(no_tags)).strip()


def _yahoo_feed(ticker: str) -> str:
    return (f"https://feeds.finance.yahoo.com/rss/2.0/headline"
            f"?s={ticker}&region=US&lang=en-US")


def _google_news_feed(query: str) -> str:
    q = urllib.parse.quote(query)
    return (f"https://news.google.com/rss/search?q={q}"
            f"&hl=en-US&gl=US&ceid=US:en")


def build_feeds() -> dict:
    """조회할 피드 목록 {소스명: url}. 유니버스에서 뽑는다 — 하드코딩 금지.

    소스명 접두어(ticker: / theme:)는 section 으로 쓰여 나중에 출처를 구분한다.
    """
    feeds = {}
    for tk in intel_sources.feed_tickers():
        feeds[f"ticker:{tk}"] = _yahoo_feed(tk)
    for theme, query in intel_sources.theme_queries().items():
        feeds[f"theme:{theme}"] = _google_news_feed(query)
    return feeds


STAGING_DIR = STATE_DIR / "intel_staging"
SEEN_URLS_FILE = STAGING_DIR / "seen_urls.txt"


def load_seen_urls() -> set:
    """기존 seen_urls.txt 에서 URL 세트 로드."""
    if SEEN_URLS_FILE.exists():
        with open(SEEN_URLS_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def save_seen_urls(urls: set) -> None:
    """seen_urls.txt 에 URL 세트 저장."""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    with open(SEEN_URLS_FILE, "w", encoding="utf-8") as f:
        for url in sorted(urls):
            f.write(url + "\n")


def load_staging_records(quarter: str) -> list[dict]:
    """기존 staging jsonl 에서 모든 레코드 로드."""
    path = STAGING_DIR / f"{quarter}.jsonl"
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def save_staging_records(quarter: str, records: list[dict]) -> None:
    """staging jsonl 에 레코드 저장 (append)."""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    path = STAGING_DIR / f"{quarter}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def fetch_feed(feed_name: str, url: str) -> list[dict] | None:
    """RSS 피드 다운로드 & 파싱. 실패 시 None 반환."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            content = response.read()
        root = ET.fromstring(content)
        items = []
        for item in root.findall(".//item"):
            title_elem = item.find("title")
            link_elem = item.find("link")
            pub_elem = item.find("pubDate")
            desc_elem = item.find("description")

            title = title_elem.text if title_elem is not None and title_elem.text else ""
            link = link_elem.text if link_elem is not None and link_elem.text else ""
            published = pub_elem.text if pub_elem is not None and pub_elem.text else ""
            # Google News 요약은 HTML — 걷어야 href 속 google.com 오탐이 사라진다.
            summary = _strip_html(desc_elem.text) if desc_elem is not None and desc_elem.text else ""

            if title and link:
                items.append({
                    "section": feed_name,
                    "title": title,
                    "url": link,
                    "published": published,
                    "summary": summary,
                })
        return items
    except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
        print(f"[ERROR] {feed_name}: {e}", file=sys.stderr)
        return None


def main():
    """메인 수집 로직."""
    print("=== 뉴스 수집 (Yahoo 티커 + Google News 테마) ===")
    print(f"작업 분기: {quarter_of(date.today())}\n")

    seen_urls = load_seen_urls()
    print(f"기존 seen_urls 로드: {len(seen_urls)} 건\n")

    feeds = build_feeds()
    print(f"피드 {len(feeds)}개 (티커별 Yahoo + 테마별 Google News)\n")

    all_fetched = []
    all_filtered = []
    ok_feeds = 0

    for feed_name, feed_url in feeds.items():
        items = fetch_feed(feed_name, feed_url)
        if items is None:
            print(f"[{feed_name}] 수집 실패 (스킵)")
            continue
        ok_feeds += 1
        # 오래된 기사 제거 — Google News 가 관련도순으로 수개월치를 섞어 보낸다.
        recent = [it for it in items
                  if (_age_days(it["published"]) or 0) <= MAX_AGE_DAYS]
        all_fetched.extend(recent)

        # 티커 피드는 그 티커를 이미 알고 조회했으므로, 제목에 사명이 안 나와도
        # 그 티커로 태그를 보장한다. 그 위에 match_relevance 로 추가 티커·테마를 얹는다.
        seed = feed_name.split(":", 1)[1] if feed_name.startswith("ticker:") else None
        filtered = []
        for item in recent:
            matched = intel_sources.match_relevance(item["title"], item["summary"])
            if seed and seed not in matched:
                matched = [seed] + matched
            if matched:
                filtered.append(intel_sources.to_staging_record(
                    section=feed_name, title=item["title"], url=item["url"],
                    published=item["published"], summary=item["summary"],
                    matched=matched,
                ))
        all_filtered.extend(filtered)

    print(f"전체 수집 요약: 피드 {ok_feeds}/{len(feeds)} 성공 · "
          f"수집 {len(all_fetched)} · 관련 {len(all_filtered)}\n")

    # 신선도 가드 — 발행일 범위를 항상 출력한다. WSJ 동결 사고(2025-01 고정)처럼
    # HTTP 는 성공하는데 내용만 묵는 경우를 사람이 눈으로 잡을 수 있게.
    # ⚠ RFC822 문자열은 요일명으로 시작해 문자열 정렬이 안 된다 — 반드시 날짜로 정렬.
    dated = [(d, x["published"]) for x in all_fetched
             if (d := _pub_dt(x.get("published", ""))) is not None]
    if dated:
        dated.sort(key=lambda t: t[0])
        print(f"발행일 범위: {dated[0][1][:16]}  ~  {dated[-1][1][:16]}")
        _freshness_warn(dated[-1][1])
        print()

    # 중복 제거
    deduped = intel_sources.dedup(all_filtered, seen_urls)
    print(f"중복 제거:")
    print(f"  신규: {len(deduped)} 건\n")

    if not deduped:
        print("신규 뉴스 없음. 종료.")
        return

    # staging 에 적재
    quarter = quarter_of(date.today())
    save_staging_records(quarter, deduped)
    print(f"'{quarter}.jsonl' 에 {len(deduped)} 건 append 완료")

    # seen_urls 갱신
    new_urls = {r["url"] for r in deduped}
    seen_urls.update(new_urls)
    save_seen_urls(seen_urls)
    print(f"seen_urls.txt 갱신: {len(seen_urls)} 건\n")

    print("=== 수집 완료 ===")


if __name__ == "__main__":
    main()
