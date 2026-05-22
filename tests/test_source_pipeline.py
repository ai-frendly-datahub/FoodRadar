from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests
import yaml
from radar_core import CrawlHealthStore

from foodradar.collector import (
    _collect_single,
    _detect_encoding,
    _extract_datetime,
    _fetch_url_with_retry,
    _parse_retry_after,
    _source_max_attempts,
    _source_request_timeout,
    collect_sources,
)
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
    assert "Product" in data_quality["quality_outputs"]["alias_entities"]
    assert "recall_status_change" in data_quality["event_models"]
    assert data_quality["canonical_keys"]["product"]["fields"]
    assert "Bibigo Dumplings" in data_quality["alias_map"]["Product"]["비비고 만두"]
    assert "CJ CheilJedang" in data_quality["alias_map"]["Brand"]["CJ"]
    assert "Samyang Foods" in data_quality["alias_map"]["Manufacturer"]["삼양식품"]

    config = load_category_config("food")
    entities = {entity.name: set(entity.keywords) for entity in config.entities}
    assert {
        "tea",
        "tequila",
        "국밥",
        "ganjang gejang",
        "guanciale",
        "pumpkin",
        "burger",
    } <= entities["FoodType"]
    assert {"Sazerac", "Pure Leaf", "PepsiCo", "Ferrara"} <= entities["Brand"]
    assert {"mold", "undeclared", "air bubbles"} <= entities["SafetyIssue"]

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
    with CrawlHealthStore(str(health_db_path), batch_size=1, failure_threshold=1) as store:
        health = store.get_health("official")
    assert health is not None
    assert health.disabled is False
    assert health.failure_count == 0


def test_collect_sources_clears_stale_crawl_health_error_after_success(tmp_path: Path) -> None:
    health_db_path = tmp_path / "health.duckdb"
    with CrawlHealthStore(str(health_db_path), batch_size=1, failure_threshold=10) as store:
        store.record_failure("official", "previous timeout", 1.0)

    source = Source(
        name="official",
        type="rss",
        url="https://example.com/feed",
    )
    article = Article(
        title="official",
        link="https://example.com/official-success",
        summary="official",
        published=None,
        source="official",
        category="food",
    )
    manager = _pass_through_manager()

    with (
        patch("foodradar.collector._collect_single", return_value=[article]),
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
    with CrawlHealthStore(str(health_db_path), batch_size=1, failure_threshold=10) as store:
        health = store.get_health("official")
    assert health is not None
    assert health.disabled is False
    assert health.failure_count == 0
    assert health.last_error is None


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


def test_collect_single_uses_date_only_summary_as_published_fallback() -> None:
    source = Source(
        name="official enforcement",
        type="rss",
        url="https://example.com/feed",
    )
    response = Mock()
    response.content = b"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Administrative action</title>
      <link>https://example.com/enforcement</link>
      <description>20260522</description>
    </item>
  </channel>
</rss>"""
    response.headers = {"Content-Type": "application/rss+xml; charset=utf-8"}

    with patch("foodradar.collector._fetch_url_with_retry", return_value=response):
        articles = _collect_single(source, category="food", limit=1, timeout=5)

    assert articles[0].published is not None
    assert articles[0].published.date().isoformat() == "2026-05-22"
    assert articles[0].summary == "20260522"


def test_collect_single_normalizes_official_enforcement_date_summary() -> None:
    source = Source(
        name="official enforcement",
        type="rss",
        url="https://example.com/feed",
        config={"event_model": "enforcement_action"},
    )
    long_reason = (
        "2024. 9. 2.부터 2026. 1. 9.까지 베트남산 수입식품을 총 48회에 "
        "걸쳐 수입신고하면서 해외 제조업소 소재지를 사실과 다르게 수입신고한 "
        "사실이 있음. 추가 처분 사유 본문입니다. 수입신고한 제조업소와 실제 "
        "제조업소 회신 내용이 서로 달라 수입식품안전관리 특별법 위반으로 "
        "행정처분 대상이 된 사실이 있음."
    )
    response = Mock()
    response.content = f"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <item>
      <title>{long_reason}</title>
      <link>https://example.com/enforcement</link>
      <description>20260522</description>
    </item>
  </channel>
</rss>""".encode()
    response.headers = {"Content-Type": "application/rss+xml; charset=utf-8"}

    with patch("foodradar.collector._fetch_url_with_retry", return_value=response):
        articles = _collect_single(source, category="food", limit=1, timeout=5)

    assert articles[0].published == datetime(2026, 5, 22, tzinfo=UTC)
    assert articles[0].summary == long_reason
    assert articles[0].title.endswith("…")
    assert len(articles[0].title) <= 160


def test_collect_single_normalizes_official_enforcement_placeholder_title() -> None:
    source = Source(
        name="식품안전나라 행정처분",
        type="rss",
        url="https://example.com/feed",
        config={"event_model": "enforcement_action"},
    )
    response = Mock()
    response.content = b"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <item>
      <link>https://example.com/enforcement-placeholder</link>
      <description>20260520</description>
    </item>
  </channel>
</rss>"""
    response.headers = {"Content-Type": "application/rss+xml; charset=utf-8"}

    with patch("foodradar.collector._fetch_url_with_retry", return_value=response):
        articles = _collect_single(source, category="food", limit=1, timeout=5)

    assert articles[0].published == datetime(2026, 5, 20, tzinfo=UTC)
    assert articles[0].title == "식품안전나라 행정처분 2026-05-20"
    assert articles[0].summary == "식품안전나라 행정처분 공고일 2026-05-20"


def test_extract_datetime_treats_feedparser_struct_time_as_utc(monkeypatch) -> None:
    original_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Asia/Seoul")
    if hasattr(time, "tzset"):
        time.tzset()

    try:
        parsed = time.struct_time((2026, 5, 22, 0, 30, 0, 4, 142, 0))

        result = _extract_datetime({"published_parsed": parsed})

        assert result == datetime(2026, 5, 22, 0, 30, tzinfo=UTC)
    finally:
        if original_tz is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", original_tz)
        if hasattr(time, "tzset"):
            time.tzset()


def test_extract_datetime_uses_updated_parsed_and_header_strings() -> None:
    updated = time.struct_time((2026, 5, 21, 12, 30, 0, 3, 141, 0))

    assert _extract_datetime({"updated_parsed": updated}) == datetime(
        2026, 5, 21, 12, 30, tzinfo=UTC
    )
    assert _extract_datetime({"published": "Wed, 21 May 2026 12:30:00 GMT"}) == datetime(
        2026, 5, 21, 12, 30, tzinfo=UTC
    )
    assert _extract_datetime({"published": "not a date"}) is None


def test_detect_encoding_prefers_declared_korean_and_charset_headers() -> None:
    response = Mock()
    response.headers = {"Content-Type": "text/xml; charset=euc-kr"}
    assert _detect_encoding(response) == "euc-kr"

    response.headers = {"Content-Type": "application/rss+xml; charset=iso-8859-1"}
    assert _detect_encoding(response) == "iso-8859-1"

    response.headers = {}
    assert _detect_encoding(response) == "utf-8"


def test_collect_single_decodes_euc_kr_description() -> None:
    source = Source(name="korean", type="rss", url="https://example.com/feed")
    summary = "본문 내용입니다 " * 8
    response = Mock()
    response.content = f"""<?xml version="1.0" encoding="EUC-KR"?>
<rss version="2.0">
  <channel>
    <item>
      <title>제목</title>
      <link>https://example.com/ko</link>
      <description>{summary}</description>
    </item>
  </channel>
</rss>""".encode("euc-kr")
    response.headers = {"Content-Type": "application/rss+xml; charset=euc-kr"}

    with patch("foodradar.collector._fetch_url_with_retry", return_value=response):
        articles = _collect_single(source, category="food", limit=1, timeout=5)

    assert articles[0].title == "제목"
    assert articles[0].summary == summary.strip()


def test_collect_single_fetches_link_when_summary_is_short() -> None:
    source = Source(name="short", type="rss", url="https://example.com/feed")
    response = Mock()
    response.content = b"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Short summary</title>
      <link>https://example.com/article</link>
      <description>tiny</description>
    </item>
  </channel>
</rss>"""
    response.headers = {"Content-Type": "application/rss+xml; charset=utf-8"}
    extracted = Mock(content="long extracted article body " * 20)

    with (
        patch("foodradar.collector._fetch_url_with_retry", return_value=response),
        patch("foodradar.collector.extract_url_content_safe", return_value=extracted),
    ):
        articles = _collect_single(source, category="food", limit=1, timeout=5)

    assert articles[0].summary.startswith("long extracted article body")


def test_collect_single_keeps_date_only_summary_without_link_fetch() -> None:
    source = Source(name="date-only", type="rss", url="https://example.com/feed")
    response = Mock()
    response.content = b"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Date only</title>
      <link>https://example.com/date</link>
      <description>2026.05.22</description>
    </item>
  </channel>
</rss>"""
    response.headers = {"Content-Type": "application/rss+xml; charset=utf-8"}

    with (
        patch("foodradar.collector._fetch_url_with_retry", return_value=response),
        patch("foodradar.collector.extract_url_content_safe") as extract,
    ):
        articles = _collect_single(source, category="food", limit=1, timeout=5)

    extract.assert_not_called()
    assert articles[0].summary == "2026.05.22"
    assert articles[0].published == datetime(2026, 5, 22, tzinfo=UTC)


def test_source_timeout_and_attempt_helpers_reject_invalid_values() -> None:
    source = Source(
        name="invalid",
        type="rss",
        url="https://example.com/feed",
        config={"request_timeout": "bad", "max_attempts": False},
    )

    assert _source_request_timeout(source, 7) == 7
    assert _source_max_attempts(source, 4) == 4
    assert _source_request_timeout(
        Source(name="low", type="rss", url="https://example.com", config={"timeout": 0}),
        7,
    ) == 1
    assert _source_max_attempts(
        Source(name="low", type="rss", url="https://example.com", config={"max_attempts": 0}),
        4,
    ) == 1


def test_fetch_url_with_retry_records_success_and_retry_after() -> None:
    response = Mock()
    response.status_code = 200
    response.headers = {}
    response.raise_for_status.return_value = None
    session = Mock()
    session.get.return_value = response
    throttler = Mock()
    throttler.get_current_delay.return_value = 1.5
    health_store = Mock()

    result = _fetch_url_with_retry(
        "https://example.com",
        5,
        session=session,
        source_name="source",
        throttler=throttler,
        health_store=health_store,
        max_attempts=1,
    )

    assert result is response
    throttler.acquire.assert_called_once_with("source")
    throttler.record_success.assert_called_once_with("source")
    health_store.record_success.assert_called_once_with("source", 1.5)


def test_fetch_url_with_retry_records_failures_and_raises_last_error() -> None:
    response = Mock(status_code=429)
    response.headers = {"Retry-After": "10"}
    error = requests.exceptions.HTTPError("rate limited", response=response)
    session = Mock()
    session.get.side_effect = error
    throttler = Mock()
    throttler.get_current_delay.return_value = 2.0
    health_store = Mock()

    with pytest.raises(requests.exceptions.HTTPError):
        _fetch_url_with_retry(
            "https://example.com",
            5,
            session=session,
            source_name="source",
            throttler=throttler,
            health_store=health_store,
            max_attempts=2,
        )

    assert session.get.call_count == 2
    assert throttler.record_failure.call_count == 2
    throttler.record_failure.assert_called_with("source", retry_after=10)
    assert health_store.record_failure.call_count == 2


def test_parse_retry_after_keeps_http_date_values() -> None:
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("  ") is None
    assert _parse_retry_after("120") == 120
    assert _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") == (
        "Wed, 21 Oct 2026 07:28:00 GMT"
    )
