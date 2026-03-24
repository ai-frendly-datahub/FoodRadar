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
