from __future__ import annotations

from foodradar.analyzer import apply_entity_rules
from foodradar.config_loader import load_category_config, load_category_quality_config
from foodradar.models import Article, EntityDefinition, Source


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

    def test_alias_map_adds_product_and_manufacturer_traces(self):
        """Product and manufacturer aliases resolve to canonical food safety keys."""
        articles = [_make_article("Bibigo Dumplings recall from Samyang Foods")]
        entities = [
            EntityDefinition(name="Product", display_name="제품", keywords=["비비고 만두"]),
            EntityDefinition(name="Manufacturer", display_name="제조업체", keywords=["삼양식품"]),
        ]

        result = apply_entity_rules(
            articles,
            entities,
            alias_map={
                "Product": {"비비고 만두": ["비비고 만두", "Bibigo Dumplings"]},
                "Manufacturer": {"삼양식품": ["삼양식품", "Samyang Foods"]},
            },
        )

        assert result[0].matched_entities["ProductCanonical"] == ["비비고 만두"]
        assert result[0].matched_entities["ProductAliasTrace"] == [
            "bibigo dumplings -> 비비고 만두"
        ]
        assert result[0].matched_entities["ManufacturerCanonical"] == ["삼양식품"]
        assert result[0].matched_entities["ManufacturerAliasTrace"] == [
            "samyang foods -> 삼양식품"
        ]

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

    def test_real_config_resolves_product_and_manufacturer_aliases(self):
        """Real FoodRadar config wires Product/Manufacturer aliases into analyzer."""
        config = load_category_config("food")
        metadata = load_category_quality_config("food")
        data_quality = metadata["data_quality"]
        article = _make_article("Bibigo Dumplings recall from Samyang Foods")

        result = apply_entity_rules(
            [article],
            config.entities,
            alias_map=data_quality["alias_map"],
        )

        assert result[0].matched_entities["ProductCanonical"] == ["비비고 만두"]
        assert result[0].matched_entities["ManufacturerCanonical"] == ["삼양식품"]

    def test_official_recall_source_uses_title_and_summary_as_entities(self):
        """Official recall rows keep product/manufacturer trace even without keyword hits."""
        articles = [_make_article("주식회사 국왕푸드", summary="이부자 한우 국밥")]
        articles[0].source = "식품안전나라 회수판매중지"
        sources = [
            Source(
                name="식품안전나라 회수판매중지",
                type="rss",
                url="https://example.com/recall.xml",
                country="KR",
                trust_tier="T1_official",
                config={
                    "event_model": "recall_status_change",
                    "canonical_key_fields": [
                        "product_name",
                        "manufacturer_name",
                        "source_url",
                    ],
                },
            )
        ]

        result = apply_entity_rules(articles, [], sources=sources)

        assert result[0].matched_entities["ProductCanonical"] == ["이부자 한우 국밥"]
        assert result[0].matched_entities["ManufacturerCanonical"] == ["주식회사 국왕푸드"]

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

    def test_real_config_classifies_current_unmatched_review_items(self):
        """Current review rows classify concrete brand and meal context terms."""
        config = load_category_config("food")
        articles = [
            _make_article("Mondelēz CEO says Iran war could further weigh on consumer confidence"),
            _make_article("Assorted goodness for dinner"),
            _make_article("How Do I Cook These?"),
            _make_article("Organic ice cream recalled because of metal pieces"),
            _make_article("Danone exits stake in Lifeway Foods", summary="Kefir brand ownership update"),
            _make_article("Kraft Heinz's biggest portfolio campaign celebrates America250"),
            _make_article("Newly opened Aldi applesauce. Is this bad or just sloppy packaging?"),
            _make_article("Kimbap"),
            _make_article("jjajang jjangbbong 짜장면 짬뽕 탕수육"),
            _make_article("Bossam Jokbal Fest"),
            _make_article("Horse shaped baguettes"),
            _make_article(
                "[Podcast] Cold Chain 2026 – Evolving Under Pressure",
                summary="Forces reshaping cold chain operations and supply chain resilience.",
            ),
            _make_article("디카페인 커피 안심하고 선택하세요"),
        ]

        result = apply_entity_rules(articles, config.entities)

        assert result[0].matched_entities["Brand"] == ["mondelēz"]
        assert result[1].matched_entities["FoodGeneral"] == ["dinner"]
        assert result[2].matched_entities["FoodGeneral"] == ["cook"]
        assert result[3].matched_entities["FoodType"] == ["ice cream"]
        assert result[3].matched_entities["SafetyIssue"] == ["recalled", "metal pieces"]
        assert result[4].matched_entities["Brand"] == ["danone", "lifeway"]
        assert result[4].matched_entities["FoodType"] == ["kefir"]
        assert result[5].matched_entities["Brand"] == ["kraft heinz"]
        assert result[6].matched_entities["Brand"] == ["aldi"]
        assert result[6].matched_entities["FoodType"] == ["applesauce"]
        assert result[6].matched_entities["SafetyIssue"] == ["is this bad"]
        assert result[7].matched_entities["FoodType"] == ["kimbap"]
        assert result[8].matched_entities["FoodType"] == [
            "jjajang",
            "jjangbbong",
            "짜장면",
            "짬뽕",
            "탕수육",
        ]
        assert result[9].matched_entities["FoodType"] == ["bossam", "jokbal"]
        assert result[10].matched_entities["FoodType"] == ["baguettes"]
        assert result[11].matched_entities["FoodGeneral"] == ["cold chain", "supply chain"]
        assert result[12].matched_entities["FoodType"] == ["커피", "디카페인"]
