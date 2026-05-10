from __future__ import annotations

import json
import os

import main as food_main
from foodradar.models import CategoryConfig, Source


def test_augment_summary_with_quality_adds_alias_and_freshness_payload(tmp_path):
    summary_path = tmp_path / "food_20260429_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "date": "2026-04-29",
                "category": "food",
                "article_count": 1,
                "source_count": 1,
                "matched_count": 1,
                "top_entities": [],
                "sources": {},
            }
        ),
        encoding="utf-8",
    )
    quality_report = {
        "summary": {
            "collection_error_count": 0,
            "stale_sources": 1,
            "missing_sources": 1,
            "product_alias_candidate_count": 1,
            "manufacturer_alias_candidate_count": 1,
        },
        "sources": [
            {
                "source": "Recall RSS",
                "status": "missing",
                "event_model": "recall_status_change",
            }
        ],
        "alias_candidates": [
            {
                "alias_type": "product",
                "canonical": "비비고 만두",
                "variants": ["Bibigo Dumplings"],
            }
        ],
        "match_coverage_review_items": [
            {
                "reason": "official_recall_unclassified",
                "source": "식품안전나라 회수판매중지",
                "title": "주식회사 국왕푸드",
            }
        ],
        "events": [
            {
                "event_model": "recall_status_change",
                "product_canonical": ["비비고 만두"],
                "alias_traces": ["Bibigo Dumplings -> 비비고 만두"],
            }
        ],
    }

    food_main._augment_summary_with_quality(summary_path, quality_report)

    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["quality_summary"]["product_alias_candidate_count"] == 1
    assert payload["quality_flagged_sources"][0]["source"] == "Recall RSS"
    assert payload["quality_alias_candidates"][0]["canonical"] == "비비고 만두"
    assert payload["quality_match_coverage_review_items"][0]["reason"] == (
        "official_recall_unclassified"
    )
    assert payload["quality_alias_events"][0]["product_canonical"] == ["비비고 만두"]
    assert payload["warnings"] == ["freshness gaps detected: stale=1, missing=1"]


def test_latest_summary_path_returns_newest_summary(tmp_path):
    old_summary = tmp_path / "food_20260428_summary.json"
    new_summary = tmp_path / "food_20260429_summary.json"
    old_summary.write_text("{}", encoding="utf-8")
    new_summary.write_text("{}", encoding="utf-8")
    os.utime(old_summary, (1, 1))
    os.utime(new_summary, (2, 2))

    assert food_main._latest_summary_path(tmp_path, "food") == new_summary


def test_quality_lookback_uses_sla_floor_for_smoke_windows():
    category = CategoryConfig(
        category_name="food",
        display_name="Food Radar",
        sources=[
            Source(
                name="Recall Feed",
                type="rss",
                url="https://example.com/recall.xml",
                config={"event_model": "recall_status_change", "freshness_sla_days": 3},
            ),
            Source(
                name="Complaint Feed",
                type="rss",
                url="https://example.com/complaint.xml",
                config={"event_model": "complaint_signal", "freshness_sla_hours": 12},
            ),
        ],
        entities=[],
    )
    quality_cfg = {
        "data_quality": {
            "freshness_sla": {
                "recall_status_change": {"max_age_days": 3},
                "complaint_signal_hours": 72,
            }
        }
    }

    assert (
        food_main._quality_lookback_days(
            category_cfg=category,
            quality_cfg=quality_cfg,
            recent_days=1,
        )
        == 7
    )
