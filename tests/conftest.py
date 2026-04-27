from __future__ import annotations

import pytest

from foodradar.models import Article, CategoryConfig, EntityDefinition, Source


@pytest.fixture()
def tmp_db(tmp_path):
    return tmp_path / "test.duckdb"


@pytest.fixture()
def sample_article():
    return Article(
        title="테스트 기사",
        link="https://example.com/1",
        summary="테스트 요약",
        published=None,
        source="TestSource",
        category="test",
        matched_entities={"엔티티A": ["키워드1"]},
    )


@pytest.fixture()
def sample_category():
    return CategoryConfig(
        category_name="test",
        display_name="테스트",
        sources=[Source(name="TestSource", type="rss", url="https://example.com/feed")],
        entities=[EntityDefinition(name="엔티티A", display_name="엔티티A", keywords=["키워드1"])],
    )
