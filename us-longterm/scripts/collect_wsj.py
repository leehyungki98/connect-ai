"""WSJ RSS 수집 — 관련도 필터링 후 staging에 적재.

실행: python scripts/collect_wsj.py

로직:
  1. 4개 WSJ 피드에서 item 파싱 (title, link, pubDate, description)
  2. match_relevance 로 필터 (관련 티커/매크로만)
  3. 기존 seen_urls + state/intel_staging/ jsonl 에서 중복 제거
  4. 신규만 state/intel_staging/{분기}.jsonl 에 append
  5. seen_urls.txt 갱신

점수는 절대 매기지 않는다 — status="pending" 만.
"""
import json
import sys
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


FEEDS = {
    "world": "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
    "markets": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "tech": "https://feeds.a.dj.com/rss/RSSWSJD.xml",
    "business": "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
}

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
            summary = desc_elem.text if desc_elem is not None and desc_elem.text else ""

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
    print("=== WSJ RSS 수집 ===")
    print(f"작업 분기: {quarter_of(date.today())}\n")

    seen_urls = load_seen_urls()
    print(f"기존 seen_urls 로드: {len(seen_urls)} 건\n")

    all_fetched = []
    all_filtered = []
    total_new = 0

    for feed_name, feed_url in FEEDS.items():
        print(f"[{feed_name}] {feed_url}")
        items = fetch_feed(feed_name, feed_url)

        if items is None:
            print(f"  → 수집 실패 (스킵)\n")
            continue

        print(f"  수집: {len(items)} 건")
        all_fetched.extend(items)

        # 관련도 필터링
        filtered = []
        for item in items:
            matched = intel_sources.match_relevance(item["title"], item["summary"])
            if matched:
                record = intel_sources.to_staging_record(
                    section=item["section"],
                    title=item["title"],
                    url=item["url"],
                    published=item["published"],
                    summary=item["summary"],
                    matched=matched,
                )
                filtered.append(record)
        print(f"  필터링: {len(filtered)} 건 (관련도 O)")
        all_filtered.extend(filtered)

    print(f"\n전체 수집 요약:")
    print(f"  수집: {len(all_fetched)} 건")
    print(f"  필터링: {len(all_filtered)} 건\n")

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
