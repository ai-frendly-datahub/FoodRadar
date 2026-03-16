from __future__ import annotations

from foodradar.analyzer import apply_entity_rules
from foodradar.models import Article, EntityDefinition


def _make_article(title: str, summary: str = "") -> Article:
    return Article(
        title=title,
        link=f"https://example.com/{hash(title)}",
        summary=summary,
        published=None,
        source="TestSource",
        category="test",
    )


class TestApplyEntityRules:
    """Unit tests for FoodRadar's apply_entity_rules with Korean analyzer support."""

    def test_keyword_match(self):
        """Keyword in title triggers a match."""
        articles = [_make_article("Salmonella outbreak in lettuce")]
        entities = [
            EntityDefinition(name="Salmonella", display_name="Salmonella", keywords=["salmonella"])
        ]

        result = apply_entity_rules(articles, entities)

        assert len(result) == 1
        assert "Salmonella" in result[0].matched_entities
        assert "salmonella" in result[0].matched_entities["Salmonella"]

    def test_no_match(self):
        """No match when keyword is absent."""
        articles = [_make_article("Weather forecast for tomorrow")]
        entities = [
            EntityDefinition(name="Salmonella", display_name="Salmonella", keywords=["salmonella"])
        ]

        result = apply_entity_rules(articles, entities)

        assert len(result) == 1
        assert result[0].matched_entities == {}

    def test_case_insensitive(self):
        """Matching is case-insensitive."""
        articles = [_make_article("SALMONELLA detected in factory")]
        entities = [
            EntityDefinition(name="Salmonella", display_name="Salmonella", keywords=["salmonella"])
        ]

        result = apply_entity_rules(articles, entities)

        assert "Salmonella" in result[0].matched_entities

    def test_multiple_entities(self):
        """Multiple entities can match the same article."""
        articles = [_make_article("Salmonella and listeria found in recall")]
        entities = [
            EntityDefinition(name="Salmonella", display_name="Salmonella", keywords=["salmonella"]),
            EntityDefinition(name="Listeria", display_name="Listeria", keywords=["listeria"]),
        ]

        result = apply_entity_rules(articles, entities)

        assert "Salmonella" in result[0].matched_entities
        assert "Listeria" in result[0].matched_entities

    def test_empty_articles(self):
        """Empty article list returns empty result."""
        entities = [
            EntityDefinition(name="Salmonella", display_name="Salmonella", keywords=["salmonella"])
        ]

        result = apply_entity_rules([], entities)

        assert result == []

    def test_summary_match(self):
        """Keywords in summary also trigger matches."""
        articles = [_make_article("Food safety alert", summary="Salmonella contamination detected")]
        entities = [
            EntityDefinition(name="Salmonella", display_name="Salmonella", keywords=["salmonella"])
        ]

        result = apply_entity_rules(articles, entities)

        assert "Salmonella" in result[0].matched_entities

    def test_non_ascii_keyword(self):
        """Non-ASCII (Korean) keywords match via substring."""
        articles = [_make_article("식중독 예방 가이드")]
        entities = [EntityDefinition(name="식중독", display_name="식중독", keywords=["식중독"])]

        result = apply_entity_rules(articles, entities)

        assert "식중독" in result[0].matched_entities
