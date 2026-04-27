from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from foodradar.models import Article, CategoryConfig, Source
from foodradar.quality_report import build_quality_report, write_quality_report


def test_build_quality_report_marks_fresh_stale_and_missing_sources() -> None:
    now = datetime(2026, 4, 12, 0, 0, tzinfo=UTC)
    category = CategoryConfig(
        category_name="food",
        display_name="Food Radar",
        sources=[
            Source(
                name="Recall RSS",
                type="rss",
                url="https://example.com/recall.xml",
                config={"event_model": "recall_status_change", "freshness_sla_days": 1},
            ),
            Source(
                name="Enforcement RSS",
                type="rss",
                url="https://example.com/enforcement.xml",
                config={"event_model": "enforcement_action", "freshness_sla_days": 2},
            ),
            Source(
                name="Complaint Reddit",
                type="reddit",
                url="https://www.reddit.com/r/foodsafety/",
                config={"event_model": "complaint_signal"},
            ),
        ],
        entities=[],
    )
    quality_config = {
        "data_quality": {
            "freshness_sla": {"complaint_signal": {"max_age_days": 3}},
        }
    }
    articles = [
        Article(
            title="Old recall notice",
            link="https://example.com/old-recall",
            summary="Recall notice",
            published=now - timedelta(days=3),
            collected_at=now - timedelta(hours=1),
            source="Recall RSS",
            category="food",
        ),
        Article(
            title="Fresh complaint signal",
            link="https://example.com/complaint",
            summary="Consumer complaint",
            published=now - timedelta(days=1),
            collected_at=now,
            source="Complaint Reddit",
            category="food",
        ),
    ]

    report = build_quality_report(
        category=category,
        articles=articles,
        errors=["Enforcement RSS: timeout"],
        quality_config=quality_config,
        generated_at=now,
    )

    statuses = {row["source"]: row["status"] for row in report["sources"]}
    assert statuses == {
        "Recall RSS": "stale",
        "Enforcement RSS": "missing",
        "Complaint Reddit": "fresh",
    }
    assert report["summary"]["tracked_sources"] == 3
    assert report["summary"]["stale_sources"] == 1
    assert report["summary"]["missing_sources"] == 1
    assert report["summary"]["recall_status_change_events"] == 1
    assert report["summary"]["complaint_signal_events"] == 1
    assert report["summary"]["fresh_food_events"] == 1
    assert report["summary"]["stale_food_events"] == 1
    assert report["summary"]["complaint_auxiliary_only_events"] == 1
    assert report["sources"][1]["errors"] == ["Enforcement RSS: timeout"]
    recall_event = report["events"][0]
    assert recall_event["event_model"] == "recall_status_change"
    assert recall_event["event_status"] == "stale"
    assert recall_event["recall_status"] == "recall"
    assert recall_event["notice_date"] == "2026-04-09"
    assert recall_event["food_event_key"]


def test_build_quality_report_extracts_alias_candidates() -> None:
    now = datetime(2026, 4, 12, 0, 0, tzinfo=UTC)
    category = CategoryConfig(
        category_name="food",
        display_name="Food Radar",
        sources=[
            Source(
                name="Recall RSS",
                type="rss",
                url="https://example.com/feed",
                config={"event_model": "recall_status_change"},
            )
        ],
        entities=[],
    )
    articles = [
        Article(
            title="CJ recall",
            link="https://example.com/1",
            summary="",
            published=now,
            source="Recall RSS",
            category="food",
            matched_entities={"Brand": ["CJ"]},
        ),
        Article(
            title="CJ Corp recall",
            link="https://example.com/2",
            summary="",
            published=now,
            source="Recall RSS",
            category="food",
            matched_entities={"Brand": ["CJ Corp."]},
        ),
    ]

    report = build_quality_report(category=category, articles=articles, generated_at=now)

    assert report["summary"]["alias_candidate_count"] == 1
    candidate = report["alias_candidates"][0]
    assert candidate["alias_type"] == "brand"
    assert candidate["normalized"] == "cj"
    assert candidate["variants"] == ["CJ", "CJ Corp."]


def test_build_quality_report_extracts_mapped_alias_trace() -> None:
    now = datetime(2026, 4, 12, 0, 0, tzinfo=UTC)
    category = CategoryConfig(
        category_name="food",
        display_name="Food Radar",
        sources=[
            Source(
                name="Recall RSS",
                type="rss",
                url="https://example.com/feed",
                config={"event_model": "recall_status_change"},
            )
        ],
        entities=[],
    )
    articles = [
        Article(
            title="CJ CheilJedang recall",
            link="https://example.com/1",
            summary="",
            published=now,
            source="Recall RSS",
            category="food",
            matched_entities={
                "Brand": ["cj cheiljedang"],
                "BrandCanonical": ["CJ"],
                "BrandAliasTrace": ["cj cheiljedang -> CJ"],
            },
        ),
    ]

    report = build_quality_report(category=category, articles=articles, generated_at=now)

    candidate = report["alias_candidates"][0]
    assert candidate["alias_type"] == "brand"
    assert candidate["canonical"] == "CJ"
    assert candidate["variants"] == ["cj cheiljedang"]
    assert report["summary"]["event_alias_trace_count"] == 1
    assert report["events"][0]["brand_canonical"] == ["CJ"]
    assert report["events"][0]["alias_traces"] == ["cj cheiljedang -> CJ"]


def test_build_quality_report_keeps_notice_date_separate_from_sanction_range() -> None:
    now = datetime(2026, 4, 14, 12, 0, tzinfo=UTC)
    category = CategoryConfig(
        category_name="food",
        display_name="Food Radar",
        sources=[
            Source(
                name="Enforcement RSS",
                type="rss",
                url="https://example.com/enforcement.xml",
                country="KR",
                trust_tier="T1_official",
                config={"event_model": "enforcement_action", "freshness_sla_days": 2},
            )
        ],
        entities=[],
    )
    article = Article(
        title="영업정지 기간(2025. 8. 7. ~ 2025. 8. 21.) 중 영업 계속",
        link="https://example.com/enforcement",
        summary="20260414",
        published=None,
        collected_at=now,
        source="Enforcement RSS",
        category="food",
    )

    report = build_quality_report(category=category, articles=[article], generated_at=now)

    event = report["events"][0]
    assert event["event_at"] == "2026-04-14T00:00:00+00:00"
    assert event["sanction_start_date"] == "2025-08-07"
    assert event["sanction_end_date"] == "2025-08-21"
    assert event["verification_status"] == "official_source"


def test_build_quality_report_uses_observed_collected_at_when_published_is_missing() -> None:
    now = datetime(2026, 4, 23, 3, 0, tzinfo=UTC)
    category = CategoryConfig(
        category_name="food",
        display_name="Food Radar",
        sources=[
            Source(
                name="Enforcement RSS",
                type="rss",
                url="https://example.com/enforcement.xml",
                country="KR",
                trust_tier="T1_official",
                config={
                    "event_model": "enforcement_action",
                    "event_date_field": "published",
                    "observed_date_field": "collected_at",
                    "freshness_sla_days": 2,
                },
            )
        ],
        entities=[],
    )
    article = Article(
        title="영업정지 기간(2025. 8. 7. ~ 2025. 8. 21.) 중 영업 계속",
        link="https://example.com/enforcement",
        summary="처분기간 안내",
        published=None,
        collected_at=now,
        source="Enforcement RSS",
        category="food",
    )

    report = build_quality_report(category=category, articles=[article], generated_at=now)

    assert report["sources"][0]["status"] == "fresh"
    event = report["events"][0]
    assert event["event_at"] == "2026-04-23T03:00:00+00:00"
    assert event["sanction_start_date"] == "2025-08-07"
    assert event["sanction_end_date"] == "2025-08-21"


def test_build_quality_report_excludes_disabled_sources_from_active_tracked_count() -> None:
    now = datetime(2026, 4, 23, 3, 0, tzinfo=UTC)
    category = CategoryConfig(
        category_name="food",
        display_name="Food Radar",
        sources=[
            Source(
                name="Disabled Reddit",
                type="reddit",
                url="https://www.reddit.com/r/ProductRecalls/",
                enabled=False,
                config={"event_model": "complaint_signal"},
            )
        ],
        entities=[],
    )

    report = build_quality_report(category=category, articles=[], generated_at=now)

    assert report["summary"]["tracked_sources"] == 0
    assert report["summary"]["skipped_disabled_sources"] == 1
    assert report["sources"][0]["tracked"] is False
    assert report["sources"][0]["status"] == "skipped_disabled"


def test_build_quality_report_exposes_disabled_source_skip_metadata() -> None:
    now = datetime(2026, 4, 23, 3, 0, tzinfo=UTC)
    category = CategoryConfig(
        category_name="food",
        display_name="Food Radar",
        sources=[
            Source(
                name="Disabled Reddit",
                type="reddit",
                url="https://www.reddit.com/r/ProductRecalls/",
                enabled=False,
                config={
                    "event_model": "complaint_signal",
                    "skip_reason": "empty live collection",
                    "reenable_gate": "stable non-empty feed",
                },
            )
        ],
        entities=[],
    )

    report = build_quality_report(category=category, articles=[], generated_at=now)

    assert report["sources"][0]["skip_reason"] == "empty live collection"
    assert report["sources"][0]["reenable_gate"] == "stable non-empty feed"


def test_write_quality_report_writes_latest_and_dated_json(tmp_path) -> None:
    report = {
        "category": "food",
        "generated_at": "2026-04-12T00:00:00+00:00",
        "summary": {"stale_sources": 0},
        "sources": [],
        "alias_candidates": [],
        "errors": [],
    }

    paths = write_quality_report(report, output_dir=tmp_path, category_name="food")

    assert paths["latest"] == tmp_path / "food_quality.json"
    assert paths["dated"] == tmp_path / "food_20260412_quality.json"
    assert json.loads(paths["latest"].read_text(encoding="utf-8"))["summary"] == {
        "stale_sources": 0
    }
