"""정세 소스 필터링 & 정규화 — RSS 수집·중복제거·관련도 판정.

이 모듈은 네트워크 호출 없이 순수 텍스트 처리만 한다.
- match_relevance: 뉴스 제목+요약에서 관심 티커/매크로 키워드 추출
- to_staging_record: RSS 아이템을 정규화된 dict로
- dedup: 기존 URL 중복 제거
"""

TICKER_ALIASES = {
    "NVDA": ["nvidia", "nvda"],
    "TSM": ["tsmc", "taiwan semiconductor", "tsm"],
    "META": ["meta platforms", "facebook", "instagram", "whatsapp", "mark zuckerberg"],
}

MACRO_KEYWORDS = [
    "taiwan", "semiconductor", "chip", "export control",
    "artificial intelligence", " ai ", "federal reserve", "interest rate",
    "inflation", "antitrust", "tariff", "china tech"
]


def match_relevance(title: str, summary: str) -> list[str]:
    """제목+요약에서 관심 티커/키워드 찾기.

    반환:
      - 매칭된 티커들 (예: ["NVDA", "TSM"])
      - 티커는 안 걸리고 매크로 키워드만 걸리면 "MACRO" 포함
      - 아무것도 안 걸리면 빈 리스트 (무관 뉴스, 버린다)
    """
    text = (title + " " + summary).lower()
    matched = set()

    # 티커 별칭 매칭
    for ticker, aliases in TICKER_ALIASES.items():
        for alias in aliases:
            if alias in text:
                matched.add(ticker)
                break

    # 매크로 키워드 매칭 (공백 기반 단어 경계)
    macro_matched = False
    for keyword in MACRO_KEYWORDS:
        if keyword in text:
            macro_matched = True
            break

    if macro_matched:
        matched.add("MACRO")

    return sorted(list(matched))


def to_staging_record(section: str, title: str, url: str, published: str,
                      summary: str, matched: list[str]) -> dict:
    """RSS 아이템을 정규화된 staging dict로 변환.

    인자:
      section: RSS 피드명 (world, markets, tech, business)
      title: 뉴스 제목
      url: 기사 URL
      published: 발행일 (RFC 2822 형식 또는 ISO 형식)
      summary: 요약 텍스트
      matched: match_relevance 결과 (리스트)

    반환:
      status="pending" 인 dict. 사람이 리뷰하면 "recorded" 또는 "skipped" 으로 갱신됨.
    """
    return {
        "section": section,
        "title": title,
        "url": url,
        "published": published,
        "summary": summary,
        "matched": matched,
        "status": "pending",
    }


def dedup(records: list[dict], seen_urls: set) -> list[dict]:
    """기존 URL 집합에 없는 레코드만 반환 (중복 제거).

    인자:
      records: to_staging_record 리스트
      seen_urls: 이미 수집한 URL 집합

    반환:
      seen_urls에 없는 레코드만.
    """
    return [r for r in records if r["url"] not in seen_urls]
