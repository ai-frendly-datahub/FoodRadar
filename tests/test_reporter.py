from __future__ import annotations

from datetime import UTC, datetime

import pytest

from foodradar.models import Article, CategoryConfig
from foodradar.reporter import generate_index_html, generate_report


@pytest.fixture()
def fixed_now():
    return datetime(2024, 3, 15, 9, 30, tzinfo=UTC)


@pytest.fixture()
def patch_datetime(monkeypatch, fixed_now):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    monkeypatch.setattr("radar_core.report_utils.datetime", FixedDateTime)


@pytest.fixture()
def report_articles(fixed_now):
    return [
        Article(
            title="Food Recall Notice",
            link="https://example.com/food1",
            summary="Major food recall issued.",
            published=fixed_now,
            source="FoodNews",
            category="food",
            matched_entities={"Recall": ["recall"]},
            collected_at=fixed_now,
        ),
    ]


@pytest.fixture()
def report_category():
    return CategoryConfig(
        category_name="food",
        display_name="Food Radar",
        sources=[],
        entities=[],
    )


@pytest.fixture()
def report_stats():
    return {"sources": 1, "collected": 1, "matched": 1, "window_days": 7}


class TestGenerateReport:
    """Unit tests for generate_report."""

    def test_generate_report_creates_file(
        self, tmp_path, report_category, report_articles, report_stats, patch_datetime
    ):
        """Report file is created at the specified path."""
        output = tmp_path / "reports" / "food_report.html"
        result = generate_report(
            category=report_category,
            articles=report_articles,
            output_path=output,
            stats=report_stats,
        )
        assert result == output
        assert output.exists()

    def test_generate_report_html_content(
        self, tmp_path, report_category, report_articles, report_stats, patch_datetime
    ):
        """Generated HTML contains expected content."""
        output = tmp_path / "reports" / "food_report.html"
        generate_report(
            category=report_category,
            articles=report_articles,
            output_path=output,
            stats=report_stats,
        )
        html = output.read_text(encoding="utf-8")
        assert "Food Radar" in html
        assert "Food Recall Notice" in html

    def test_generate_report_with_errors(
        self, tmp_path, report_category, report_articles, report_stats, patch_datetime
    ):
        """Error messages appear in the report HTML."""
        output = tmp_path / "reports" / "food_report.html"
        generate_report(
            category=report_category,
            articles=report_articles,
            output_path=output,
            stats=report_stats,
            errors=["source timeout"],
        )
        html = output.read_text(encoding="utf-8")
        assert "source timeout" in html

    def test_generate_report_truncates_display_outliers(
        self, tmp_path, report_category, report_stats, patch_datetime
    ):
        """Long source text is preserved upstream but compacted in HTML output."""
        long_title = "T" * 240
        long_summary = "S" * 1200
        output = tmp_path / "reports" / "food_report.html"
        generate_report(
            category=report_category,
            articles=[
                Article(
                    title=long_title,
                    link="https://example.com/long",
                    summary=long_summary,
                    published=datetime(2024, 3, 15, 9, 30, tzinfo=UTC),
                    source="FoodNews",
                    category="food",
                )
            ],
            output_path=output,
            stats=report_stats,
        )

        html = output.read_text(encoding="utf-8")
        assert long_title not in html
        assert long_summary not in html
        assert ("T" * 179) + "…" in html
        assert "S" * 700 not in html
        assert "S" * 120 in html

    def test_generate_report_includes_quality_traceability(
        self, tmp_path, report_category, report_articles, report_stats, patch_datetime
    ):
        """Quality report summary and alias candidates appear in the HTML report."""
        output = tmp_path / "reports" / "food_report.html"
        quality_report = {
            "summary": {
                "fresh_sources": 2,
                "stale_sources": 1,
                "missing_sources": 1,
                "alias_candidate_count": 1,
                "product_alias_candidate_count": 1,
                "manufacturer_alias_candidate_count": 1,
                "event_alias_trace_count": 1,
                "match_coverage_review_item_count": 1,
                "recall_status_change_events": 1,
                "enforcement_action_events": 0,
                "complaint_signal_events": 1,
            },
            "sources": [
                {
                    "source": "Recall RSS",
                    "status": "stale",
                    "event_model": "recall_status_change",
                    "age_days": 4,
                }
            ],
            "events": [
                {
                    "event_model": "recall_status_change",
                    "event_status": "stale",
                    "title": "CJ recall notice",
                    "notice_date": "2026-04-09",
                    "product_canonical": ["비비고 만두"],
                    "manufacturer_canonical": ["CJ제일제당"],
                    "recall_status": "sales_stop",
                    "alias_traces": ["Bibigo Dumplings -> 비비고 만두"],
                },
                {
                    "event_model": "complaint_signal",
                    "event_status": "fresh",
                    "title": "consumer complaint",
                    "observed_at": "2026-04-12",
                    "verification_status": "auxiliary_only",
                },
            ],
            "alias_candidates": [
                {
                    "alias_type": "product",
                    "canonical": "비비고 만두",
                    "normalized": "비비고만두",
                    "variants": ["Bibigo Dumplings"],
                }
            ],
            "match_coverage_review_items": [
                {
                    "reason": "market_or_editorial_unclassified",
                    "priority": "medium",
                    "source": "Food Dive",
                    "title": "Pure Leaf puts mental clarity in focus",
                    "recommended_action": "review Product/FoodType/Brand keyword coverage",
                }
            ],
        }

        generate_report(
            category=report_category,
            articles=report_articles,
            output_path=output,
            stats=report_stats,
            quality_report=quality_report,
        )

        html = output.read_text(encoding="utf-8")
        assert "Quality Traceability" in html
        assert "Recall RSS" in html
        assert "recall_status_change" in html
        assert "sales_stop" in html
        assert "비비고 만두" in html
        assert "CJ제일제당" in html
        assert "auxiliary_only" in html
        assert "Bibigo Dumplings" in html
        assert "coverage review" in html
        assert "market_or_editorial_unclassified" in html
        assert "Pure Leaf puts mental clarity" in html
        assert html == "\n".join(line.rstrip() for line in html.splitlines()) + "\n"
        summaries = sorted(
            (tmp_path / "reports").glob(
                "food_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_summary.json"
            )
        )
        assert len(summaries) == 1
        summary = summaries[0].read_text(encoding="utf-8")
        assert '"repo": "FoodRadar"' in summary
        assert '"ontology_version": "0.1.0"' in summary
        assert '"food.recall_status_change"' in summary


class TestGenerateIndexHtml:
    """Unit tests for generate_index_html."""

    def test_generate_index_html(self, tmp_path):
        """Index HTML is generated listing report files."""
        report_dir = tmp_path / "reports"
        report_dir.mkdir(parents=True)
        (report_dir / "food_20240315.html").write_text("<html>food</html>", encoding="utf-8")

        index_path = generate_index_html(report_dir)

        assert index_path == report_dir / "index.html"
        assert index_path.exists()
        rendered = index_path.read_text(encoding="utf-8")
        assert "Food Radar" in rendered
        assert "food_20240315.html" in rendered

    def test_generate_index_html_empty_dir(self, tmp_path):
        """Index is generated even with no reports."""
        report_dir = tmp_path / "empty_reports"
        index_path = generate_index_html(report_dir)

        assert index_path.exists()
        rendered = index_path.read_text(encoding="utf-8")
        assert "Food Radar" in rendered
