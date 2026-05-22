#!/usr/bin/env python3
"""Run DuckDB data quality checks."""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT.parent / "radar-core"))

from radar_core.common import quality_checks as shared_quality_checks  # noqa: E402

from foodradar.analyzer import apply_entity_rules  # noqa: E402
from foodradar.config_loader import (  # noqa: E402
    load_category_config,
    load_category_quality_config,
    source_language_overrides,
)
from foodradar.quality_report import build_quality_report, write_quality_report  # noqa: E402
from foodradar.storage import RadarStorage  # noqa: E402


def _project_path(project_root: Path, raw_path: str | Path) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else project_root / path


def _load_runtime_config(project_root: Path) -> dict[str, Any]:
    raw = yaml.safe_load((project_root / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _coerce_date(value: object) -> date | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(UTC).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                return None
    return None


def _latest_article_date(db_path: Path, category_name: str) -> date | None:
    if not db_path.exists():
        return None
    try:
        with duckdb.connect(str(db_path), read_only=True) as con:
            row = con.execute(
                """
                SELECT MAX(COALESCE(published, collected_at))
                FROM articles
                WHERE category = ?
                """,
                [category_name],
            ).fetchone()
    except duckdb.Error:
        return None
    if not row:
        return None
    return _coerce_date(row[0])


def _lookback_days(target_date: date | None, *, minimum_days: int = 7) -> int:
    if target_date is None:
        return minimum_days
    age_days = (datetime.now(UTC).date() - target_date).days + 1
    return max(minimum_days, age_days)


def _table_columns(con: duckdb.DuckDBPyConnection, table_name: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info('{table_name}')").fetchall()}


def _run_storage_checks(
    con: duckdb.DuckDBPyConnection,
    *,
    category_cfg: Any | None = None,
) -> None:
    table_name = "articles"
    columns = _table_columns(con, table_name)
    total = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    total_records = int(total[0]) if total else 0
    print(f"Total records: {total_records}")

    shared_quality_checks.check_missing_fields(
        con,
        table_name=table_name,
        null_conditions={
            "title": "title IS NULL OR title = ''",
            "link": "link IS NULL OR link = ''",
            "summary": "summary IS NULL OR summary = ''",
            "published": "published IS NULL",
        },
    )
    shared_quality_checks.check_duplicate_urls(con, table_name=table_name, url_column="link")
    shared_quality_checks.check_text_lengths(
        con,
        table_name=table_name,
        text_columns=["title", "summary"],
    )
    _run_text_length_outlier_check(con, table_name=table_name)

    if "language" in columns:
        shared_quality_checks.check_language_values(
            con,
            table_name=table_name,
            language_column="language",
        )
    else:
        print("\nSkipping language check: missing column 'language'")
        if category_cfg is not None:
            _run_source_language_metadata_check(category_cfg)

    if "published" in columns:
        shared_quality_checks.check_dates(con, table_name=table_name, date_column="published")
    else:
        print("\nSkipping date check: missing column 'published'")


def _run_source_language_metadata_check(category_cfg: Any) -> None:
    sources = list(getattr(category_cfg, "sources", []) or [])
    enabled_sources = [source for source in sources if getattr(source, "enabled", True)]
    missing = [
        str(getattr(source, "name", ""))
        for source in enabled_sources
        if not str(getattr(source, "language", "") or "").strip()
    ]
    total = len(enabled_sources)
    configured = total - len(missing)
    rate = round(configured / total * 100, 1) if total else 0.0
    print("\n=== Source Language Metadata Check ===")
    print(f"  configured: {configured} / {total} ({rate}%)")
    if missing:
        print("  missing sources:")
        for source_name in missing:
            print(f"    - {source_name}")


def _run_text_length_outlier_check(
    con: duckdb.DuckDBPyConnection,
    *,
    table_name: str,
    title_threshold: int = 160,
    summary_threshold: int = 2000,
) -> None:
    row = con.execute(
        f"""
        SELECT
          COUNT(*) FILTER (WHERE length(title) > ?) AS long_titles,
          COUNT(*) FILTER (WHERE length(summary) > ?) AS long_summaries
        FROM {table_name}
        WHERE category = 'food'
        """,
        [title_threshold, summary_threshold],
    ).fetchone()
    long_titles = int(row[0]) if row else 0
    long_summaries = int(row[1]) if row else 0
    print("\n=== Text Length Outlier Check ===")
    print(f"  title > {title_threshold}: {long_titles}")
    print(f"  summary > {summary_threshold}: {long_summaries}")
    if not long_titles and not long_summaries:
        return

    for source, field, count, max_len in con.execute(
        f"""
        SELECT source, field, COUNT(*) AS count, MAX(length_value) AS max_len
        FROM (
          SELECT source, 'title' AS field, length(title) AS length_value
          FROM {table_name}
          WHERE category = 'food' AND length(title) > ?
          UNION ALL
          SELECT source, 'summary' AS field, length(summary) AS length_value
          FROM {table_name}
          WHERE category = 'food' AND length(summary) > ?
        )
        GROUP BY source, field
        ORDER BY count DESC, max_len DESC, source
        LIMIT 10
        """,
        [title_threshold, summary_threshold],
    ).fetchall():
        print(f"    - {source} {field}: {count} (max {max_len})")


def generate_quality_artifacts(
    project_root: Path = PROJECT_ROOT,
    *,
    category_name: str = "food",
) -> tuple[dict[str, Path], dict[str, Any]]:
    runtime_config = _load_runtime_config(project_root)
    db_path = _project_path(
        project_root,
        str(runtime_config.get("database_path", "data/radar_data.duckdb")),
    )
    report_dir = _project_path(
        project_root,
        str(runtime_config.get("report_dir", "reports")),
    )
    categories_dir = project_root / "config" / "categories"
    category_cfg = load_category_config(category_name, categories_dir=categories_dir)
    quality_cfg = load_category_quality_config(category_name, categories_dir=categories_dir)
    lookback_days = _lookback_days(_latest_article_date(db_path, category_cfg.category_name))

    with RadarStorage(db_path) as storage:
        recent_articles = storage.recent_articles(
            category_cfg.category_name,
            days=lookback_days,
            limit=1000,
        )

    data_quality = quality_cfg.get("data_quality")
    alias_map = data_quality.get("alias_map") if isinstance(data_quality, dict) else None
    recent_articles = apply_entity_rules(
        recent_articles,
        category_cfg.entities,
        alias_map=alias_map if isinstance(alias_map, dict) else None,
        sources=category_cfg.sources,
    )

    report = build_quality_report(
        category=category_cfg,
        articles=recent_articles,
        quality_config=quality_cfg,
    )
    paths = write_quality_report(
        report,
        output_dir=report_dir,
        category_name=category_cfg.category_name,
    )
    return paths, report


def main() -> None:
    runtime_config = _load_runtime_config(PROJECT_ROOT)
    db_path = _project_path(
        PROJECT_ROOT,
        str(runtime_config.get("database_path", "data/radar_data.duckdb")),
    )
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    category_cfg = load_category_config("food", categories_dir=PROJECT_ROOT / "config" / "categories")
    quality_cfg = load_category_quality_config(
        "food",
        categories_dir=PROJECT_ROOT / "config" / "categories",
    )
    with RadarStorage(db_path) as storage:
        _ = storage.sync_source_metadata(
            category_cfg.sources,
            category=category_cfg.category_name,
            source_languages=source_language_overrides(quality_cfg),
        )

    with duckdb.connect(str(db_path), read_only=True) as con:
        _run_storage_checks(con, category_cfg=category_cfg)

    paths, report = generate_quality_artifacts(PROJECT_ROOT)
    summary = report["summary"]
    print(f"quality_report={paths['latest']}")
    print(f"tracked_sources={summary['tracked_sources']}")
    print(f"fresh_sources={summary['fresh_sources']}")
    print(f"stale_sources={summary['stale_sources']}")
    print(f"missing_sources={summary['missing_sources']}")
    print(f"alias_candidate_count={summary['alias_candidate_count']}")


if __name__ == "__main__":
    main()
