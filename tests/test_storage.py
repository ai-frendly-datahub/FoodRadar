from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from foodradar.models import Article
from foodradar.storage import RadarStorage


class TestRadarStorage:
    """Unit tests for FoodRadar's own RadarStorage CRUD operations."""

    def test_upsert_and_query(self, tmp_db, sample_article):
        """Articles can be saved and retrieved by category."""
        with RadarStorage(tmp_db) as storage:
            storage.upsert_articles([sample_article])
            results = storage.recent_articles("test", days=7)

        assert len(results) == 1
        assert results[0].title == "테스트 기사"
        assert results[0].link == "https://example.com/1"
        assert results[0].matched_entities == {"엔티티A": ["키워드1"]}

    def test_deduplication(self, tmp_db, sample_article):
        """Duplicate links are upserted, not duplicated."""
        with RadarStorage(tmp_db) as storage:
            storage.upsert_articles([sample_article])
            updated = Article(
                title="수정된 제목",
                link="https://example.com/1",
                summary="수정된 요약",
                published=None,
                source="TestSource",
                category="test",
                matched_entities={},
            )
            storage.upsert_articles([updated])
            results = storage.recent_articles("test", days=7)

        assert len(results) == 1
        assert results[0].title == "수정된 제목"

    def test_recent_articles_days_filter(self, tmp_db):
        """Only articles within the days window are returned."""
        now = datetime.now(UTC)
        recent = Article(
            title="최근",
            link="https://example.com/recent",
            summary="",
            published=now - timedelta(days=1),
            source="S",
            category="test",
        )
        old = Article(
            title="오래된",
            link="https://example.com/old",
            summary="",
            published=now - timedelta(days=30),
            source="S",
            category="test",
        )
        with RadarStorage(tmp_db) as storage:
            storage.upsert_articles([recent, old])
            results = storage.recent_articles("test", days=7)

        assert len(results) == 1
        assert results[0].title == "최근"

    def test_delete_older_than(self, tmp_db):
        """Old articles are deleted, recent ones remain."""
        now = datetime.now(UTC)
        recent = Article(
            title="최근",
            link="https://example.com/r",
            summary="",
            published=now - timedelta(days=1),
            source="S",
            category="test",
        )
        old = Article(
            title="오래된",
            link="https://example.com/o",
            summary="",
            published=now - timedelta(days=100),
            source="S",
            category="test",
        )
        with RadarStorage(tmp_db) as storage:
            storage.upsert_articles([recent, old])
            deleted = storage.delete_older_than(days=30)
            remaining = storage.recent_articles("test", days=365)

        assert deleted == 1
        assert len(remaining) == 1
        assert remaining[0].title == "최근"

    def test_context_manager(self, tmp_db, sample_article):
        """RadarStorage works correctly as a context manager."""
        with RadarStorage(tmp_db) as storage:
            storage.upsert_articles([sample_article])
            results = storage.recent_articles("test", days=7)
            assert len(results) == 1
        # Connection closed after with-block; opening a new one should still work
        with RadarStorage(tmp_db) as storage:
            results = storage.recent_articles("test", days=7)
            assert len(results) == 1

    def test_empty_upsert(self, tmp_db):
        """Upserting an empty list is a no-op."""
        with RadarStorage(tmp_db) as storage:
            storage.upsert_articles([])
            results = storage.recent_articles("test", days=7)

        assert results == []

    def test_category_isolation(self, tmp_db):
        """Articles from different categories are isolated in queries."""
        a1 = Article(
            title="Cat A",
            link="https://example.com/a",
            summary="",
            published=None,
            source="S",
            category="alpha",
        )
        a2 = Article(
            title="Cat B",
            link="https://example.com/b",
            summary="",
            published=None,
            source="S",
            category="beta",
        )
        with RadarStorage(tmp_db) as storage:
            storage.upsert_articles([a1, a2])
            alpha = storage.recent_articles("alpha", days=7)
            beta = storage.recent_articles("beta", days=7)

        assert len(alpha) == 1
        assert alpha[0].title == "Cat A"
        assert len(beta) == 1
        assert beta[0].title == "Cat B"
