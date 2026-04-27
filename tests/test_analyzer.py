from __future__ import annotations

from foodradar.analyzer import apply_entity_rules
from foodradar.config_loader import load_category_config, load_category_quality_config
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

    def test_alias_map_adds_canonical_trace(self):
        """Alias variants match and retain canonical traceability."""
        articles = [_make_article("CJ CheilJedang noodle recall")]
        entities = [EntityDefinition(name="Brand", display_name="브랜드", keywords=["CJ"])]

        result = apply_entity_rules(
            articles,
            entities,
            alias_map={"Brand": {"CJ": ["CJ", "CJ CheilJedang", "씨제이"]}},
        )

        assert "Brand" in result[0].matched_entities
        assert result[0].matched_entities["BrandCanonical"] == ["CJ"]
        assert result[0].matched_entities["BrandAliasTrace"] == ["cj cheiljedang -> CJ"]

    def test_real_config_classifies_community_food_terms_without_safety_issue(self):
        """Recipe/community source terms are classified as food types, not safety events."""
        config = load_category_config("food")
        metadata = load_category_quality_config("food")
        data_quality = metadata["data_quality"]
        article = _make_article(
            "Spinach Artichoke Pasta",
            summary="Doenjang soup with potatoes, sausages, banchan, shrimp, and anchovies.",
        )

        result = apply_entity_rules(
            [article],
            config.entities,
            alias_map=data_quality["alias_map"],
        )

        assert set(result[0].matched_entities["FoodType"]) >= {
            "shrimp",
            "anchovies",
            "spinach",
            "artichoke",
            "potatoes",
            "sausages",
            "pasta",
            "soup",
            "banchan",
            "doenjang",
        }
        assert "SafetyIssue" not in result[0].matched_entities

    def test_real_config_classifies_fowl_plague_as_safety_issue(self):
        """Food safety disease/outbreak terms from trade media remain safety issues."""
        config = load_category_config("food")
        article = _make_article(
            "Sunday Edition: Fowl plague",
            summary="H1N1 influenza virus with genes of avian origin.",
        )

        result = apply_entity_rules([article], config.entities)

        assert result[0].matched_entities["SafetyIssue"] == [
            "influenza",
            "h1n1",
            "plague",
        ]

    def test_real_config_classifies_current_community_food_terms(self):
        """Current community/trade rows keep food entities without inventing brands."""
        config = load_category_config("food")
        articles = [
            _make_article("found in canned iced coffee"),
            _make_article(
                "Colombia reported more than 650 outbreaks in 2025",
                summary="In Costa Rica, 20 outbreaks were investigated in 2025",
            ),
            _make_article(
                "This might be a long shot but it would be helpful",
                summary="Looking for South Indian cooking channels and recipe videos.",
            ),
        ]

        result = apply_entity_rules(articles, config.entities)

        assert result[0].matched_entities["FoodType"] == ["coffee"]
        assert result[1].matched_entities["SafetyIssue"] == ["outbreaks"]
        assert result[2].matched_entities["FoodGeneral"] == ["cooking", "recipe"]
