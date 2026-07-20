"""정세 소스 필터링 테스트 — 오프라인, 네트워크 불필요."""
import pytest

from longcore import intel_sources


class TestMatchRelevance:
    """관련도 필터링 로직."""

    def test_match_nvidia_ticker(self):
        """Nvidia 별칭 매칭."""
        title = "Nvidia earnings beat expectations"
        summary = "GPU maker reports Q3 profit."
        result = intel_sources.match_relevance(title, summary)
        assert "NVDA" in result

    def test_match_tsmc_with_macro(self):
        """TSM + 매크로 키워드 둘 다 매칭."""
        title = "Taiwan semiconductor export curbs tighten"
        summary = "TSMC faces new U.S. export control restrictions"
        result = intel_sources.match_relevance(title, summary)
        assert "TSM" in result
        assert "MACRO" in result

    def test_match_meta_platforms(self):
        """Meta 별칭 매칭."""
        title = "Meta Platforms CEO Mark Zuckerberg speaks"
        summary = "Facebook and Instagram revenue surges"
        result = intel_sources.match_relevance(title, summary)
        assert "META" in result

    def test_match_macro_only(self):
        """매크로 키워드만 매칭 (티커 없음)."""
        title = "Federal Reserve considers interest rate cut"
        summary = "Inflation concerns drive policy decision"
        result = intel_sources.match_relevance(title, summary)
        assert "MACRO" in result
        assert len([t for t in result if t != "MACRO"]) == 0

    def test_irrelevant_news_returns_empty(self):
        """무관 뉴스는 빈 리스트."""
        title = "Lakers win championship"
        summary = "Basketball team celebrates historic victory"
        result = intel_sources.match_relevance(title, summary)
        assert result == []

    def test_case_insensitive(self):
        """대소문자 무시."""
        title = "NVIDIA releases new GPU architecture"
        summary = "nvda stock surges on announcement"
        result = intel_sources.match_relevance(title, summary)
        assert "NVDA" in result

    def test_multiple_matches(self):
        """여러 티커 동시 매칭."""
        title = "Nvidia and TSMC partnership expands"
        summary = "The two firms collaborate on advanced chips"
        result = intel_sources.match_relevance(title, summary)
        assert "NVDA" in result
        assert "TSM" in result

    def test_whatsapp_triggers_meta(self):
        """Meta 별칭 "whatsapp" 포함."""
        title = "WhatsApp security update announced"
        summary = "Meta updates messaging platform privacy features"
        result = intel_sources.match_relevance(title, summary)
        assert "META" in result


class TestToStagingRecord:
    """RSS 정규화."""

    def test_creates_pending_record(self):
        """status는 항상 "pending"."""
        record = intel_sources.to_staging_record(
            section="world",
            title="Test headline",
            url="https://example.com/1",
            published="2026-07-21",
            summary="Test summary",
            matched=["NVDA"]
        )
        assert record["status"] == "pending"
        assert record["title"] == "Test headline"
        assert record["url"] == "https://example.com/1"
        assert record["matched"] == ["NVDA"]

    def test_all_fields_preserved(self):
        """모든 필드 보존."""
        record = intel_sources.to_staging_record(
            section="tech",
            title="AI advancement",
            url="https://wsj.com/article/123",
            published="Mon, 21 Jul 2026 10:30:00 GMT",
            summary="New AI breakthrough reported",
            matched=["MACRO"]
        )
        assert record["section"] == "tech"
        assert record["published"] == "Mon, 21 Jul 2026 10:30:00 GMT"
        assert record["summary"] == "New AI breakthrough reported"


class TestDedup:
    """중복 제거."""

    def test_removes_seen_urls(self):
        """seen_urls에 있는 URL 제외."""
        records = [
            {"url": "https://example.com/1", "title": "Article 1"},
            {"url": "https://example.com/2", "title": "Article 2"},
            {"url": "https://example.com/3", "title": "Article 3"},
        ]
        seen = {"https://example.com/1", "https://example.com/3"}
        result = intel_sources.dedup(records, seen)
        assert len(result) == 1
        assert result[0]["title"] == "Article 2"

    def test_keeps_unseen_urls(self):
        """seen에 없는 URL 모두 반환."""
        records = [
            {"url": "https://example.com/1", "title": "Article 1"},
            {"url": "https://example.com/2", "title": "Article 2"},
        ]
        seen = set()
        result = intel_sources.dedup(records, seen)
        assert len(result) == 2

    def test_empty_seen_keeps_all(self):
        """빈 seen 세트면 모두 반환."""
        records = [
            {"url": "https://a.com", "title": "A"},
            {"url": "https://b.com", "title": "B"},
        ]
        result = intel_sources.dedup(records, set())
        assert len(result) == 2
