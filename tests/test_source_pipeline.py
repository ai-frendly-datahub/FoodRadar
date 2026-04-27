from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from radar_core import CrawlHealthStore

from foodradar.collector import _collect_single, collect_sources
from foodradar.config_loader import load_category_config, load_category_quality_config
from foodradar.models import Article, Source


def _write_yaml(path: Path, data: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _pass_through_manager() -> Mock:
    breaker = Mock()
    breaker.call.side_effect = lambda func, *args, **kwargs: func(*args, **kwargs)
    manager = Mock()
    manager.get_breaker.return_value = breaker
    return manager


def test_load_category_config_preserves_source_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("GOV_API_KEY", "test-key")
    cat_dir = tmp_path / "categories"
    _write_yaml(
        cat_dir / "food.yaml",
        {
            "category_name": "food",
            "sources": [
                {
                    "name": "Food MCP",
                    "type": "mcp",
                    "url": "https://github.com/example/food-mcp",
                    "enabled": False,
                    "country": "KR",
                    "trust_tier": "T1_official",
                    "collection_tier": "C4_api",
                    "info_purpose": ["nutrition", "reference"],
                    "notes": "catalog only",
                    "config": {"env": {"GOV_API_KEY": "${GOV_API_KEY}"}},
                }
            ],
            "entities": [],
        },
    )

    config = load_category_config("food", categories_dir=cat_dir)
    source = config.sources[0]

    assert source.enabled is False
    assert source.country == "KR"
    assert source.trust_tier == "T1_official"
    assert source.collection_tier == "C4_api"
    assert source.info_purpose == ["nutrition", "reference"]
    assert source.notes == "catalog only"
    assert source.config["env"]["GOV_API_KEY"] == "test-key"


def test_real_food_config_exposes_data_quality_overlay() -> None:
    metadata = load_category_quality_config("food")

    data_quality = metadata["data_quality"]
    assert isinstance(data_quality, dict)
    assert data_quality["priority"] == "P0"
    assert data_quality["primary_motion"] == "compliance-risk"
    assert data_quality["weakest_dimension"] == "traceability"
    assert data_quality["quality_outputs"]["freshness_report"] == "reports/food_quality.json"
    assert "recall_status_change" in data_quality["event_models"]
    assert data_quality["canonical_keys"]["product"]["fields"]
    assert "CJ CheilJedang" in data_quality["alias_map"]["Brand"]["CJ"]

    backlog = metadata["source_backlog"]
    assert isinstance(backlog, dict)
    complaint_candidates = {
        candidate["id"] for candidate in backlog["consumer_complaint_candidates"]
    }
    assert complaint_candidates >= {
        "consumer24_recall_and_damage",
        "ccn_1372_consumer_counsel",
    }


def test_real_food_sources_model_recall_and_complaint_signals() -> None:
    config = load_category_config("food")
    sources = {source.name: source for source in config.sources}

    recall = sources["식품안전나라 회수판매중지"]
    assert recall.producer_role == "government"
    assert "recall_event" in recall.info_purpose
    assert recall.config["event_model"] == "recall_status_change"
    assert recall.config["observed_date_field"] == "collected_at"
    assert recall.config["canonical_key_fields"]

    enforcement = sources["식품안전나라 행정처분"]
    assert enforcement.config["event_model"] == "enforcement_action"
    assert "enforcement_action" in enforcement.info_purpose
    assert enforcement.config["request_timeout"] == 15
    assert enforcement.config["max_attempts"] == 1
    assert enforcement.config["bypass_crawl_health"] is True

    complaint = sources["r/foodsafety"]
    assert complaint.config["event_model"] == "complaint_signal"
    assert complaint.config["merge_policy"] == "do_not_merge_with_official_notice"
    assert "auxiliary_verification" in complaint.info_purpose


def test_collect_sources_routes_reddit_and_reports_unsupported() -> None:
    sources = [
        Source(name="rss", type="rss", url="https://example.com/feed"),
        Source(name="reddit", type="reddit", url="https://www.reddit.com/r/test/"),
        Source(name="catalog", type="mcp", url="https://github.com/example/food-mcp"),
        Source(
            name="disabled",
            type="rss",
            url="https://disabled.example.com/feed",
            enabled=False,
        ),
    ]
    manager = _pass_through_manager()

    rss_article = Article(
        title="rss",
        link="https://example.com/rss",
        summary="rss",
        published=None,
        source="rss",
        category="food",
    )
    reddit_article = Article(
        title="reddit",
        link="https://example.com/reddit",
        summary="reddit",
        published=None,
        source="reddit",
        category="food",
    )

    with (
        patch("foodradar.collector._collect_single", return_value=[rss_article]) as mock_rss,
        patch(
            "radar_core.collect_reddit_sources",
            return_value=([reddit_article], []),
        ) as mock_reddit,
        patch("foodradar.collector.get_circuit_breaker_manager", return_value=manager),
    ):
        articles, errors = collect_sources(
            sources,
            category="food",
            min_interval_per_host=0.0,
            max_workers=1,
        )

    assert [article.source for article in articles] == ["rss", "reddit"]
    assert mock_rss.call_count == 1
    assert mock_reddit.call_count == 1
    assert all("disabled" not in error for error in errors)
    assert any("cataloged but not collected" in error for error in errors)


def test_collect_sources_can_bypass_stale_crawl_health_disable(tmp_path: Path) -> None:
    health_db_path = tmp_path / "health.duckdb"
    with CrawlHealthStore(str(health_db_path), batch_size=1, failure_threshold=1) as store:
        store.record_failure("official", "previous timeout", 1.0)

    source = Source(
        name="official",
        type="rss",
        url="https://example.com/feed",
        config={"bypass_crawl_health": True},
    )
    article = Article(
        title="official",
        link="https://example.com/official",
        summary="official",
        published=None,
        source="official",
        category="food",
    )
    manager = _pass_through_manager()

    with (
        patch("foodradar.collector._collect_single", return_value=[article]) as mock_rss,
        patch("foodradar.collector.get_circuit_breaker_manager", return_value=manager),
    ):
        articles, errors = collect_sources(
            [source],
            category="food",
            min_interval_per_host=0.0,
            max_workers=1,
            health_db_path=str(health_db_path),
        )

    assert articles == [article]
    assert errors == []
    assert mock_rss.call_count == 1


def test_collect_single_uses_source_timeout_and_attempt_overrides() -> None:
    source = Source(
        name="slow official",
        type="rss",
        url="https://example.com/feed",
        config={"request_timeout": 12, "max_attempts": 1},
    )
    response = Mock()
    response.content = b"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Notice</title>
      <link>https://example.com/notice</link>
      <description>Summary</description>
    </item>
  </channel>
</rss>"""
    response.headers = {"Content-Type": "application/rss+xml; charset=utf-8"}

    with patch("foodradar.collector._fetch_url_with_retry", return_value=response) as fetch:
        articles = _collect_single(source, category="food", limit=1, timeout=5)

    assert [article.title for article in articles] == ["Notice"]
    assert fetch.call_args.kwargs["max_attempts"] == 1
    assert fetch.call_args.args[1] == 12
