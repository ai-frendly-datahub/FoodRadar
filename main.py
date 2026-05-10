from __future__ import annotations

import argparse
import json
from pathlib import Path
from math import ceil
from typing import Any, cast

from radar_core.date_storage import apply_date_storage_policy
from radar_core.ontology import annotate_articles_with_ontology
from radar_core.raw_logger import RawLogger

from foodradar.analyzer import apply_entity_rules
from foodradar.collector import collect_sources
from foodradar.config_loader import (
    load_category_config,
    load_category_quality_config,
    load_settings,
)
from foodradar.logger import configure_logging, get_logger
from foodradar.quality_report import build_quality_report, write_quality_report
from foodradar.reporter import generate_index_html, generate_report
from foodradar.storage import RadarStorage


logger = get_logger(__name__)


def _latest_summary_path(report_dir: Path, category: str) -> Path | None:
    candidates = [
        path
        for path in report_dir.glob(f"{category}_*_summary.json")
        if path.is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _augment_summary_with_quality(
    summary_path: Path,
    quality_report: dict[str, Any] | None,
) -> None:
    if not summary_path.exists() or not isinstance(quality_report, dict):
        return

    quality_summary = quality_report.get("summary")
    if not isinstance(quality_summary, dict) or not quality_summary:
        return

    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    collection_errors = _summary_int(quality_summary, "collection_error_count")
    stale_sources = _summary_int(quality_summary, "stale_sources")
    missing_sources = _summary_int(quality_summary, "missing_sources")
    raw_warnings = summary.get("warnings")
    if isinstance(raw_warnings, list):
        warnings = [str(item) for item in raw_warnings if str(item)]
    elif raw_warnings:
        warnings = [str(raw_warnings)]
    else:
        warnings = []
    if collection_errors:
        warnings.append(f"collection errors detected: {collection_errors}")
    if stale_sources or missing_sources:
        warnings.append(
            f"freshness gaps detected: stale={stale_sources}, missing={missing_sources}"
        )

    summary["quality_summary"] = quality_summary
    if warnings:
        summary["warnings"] = list(dict.fromkeys(warnings))

    sources = quality_report.get("sources")
    if isinstance(sources, list):
        flagged_sources = [
            row
            for row in sources
            if isinstance(row, dict)
            and str(row.get("status")) in {"stale", "missing", "unknown_event_date"}
        ]
        if flagged_sources:
            summary["quality_flagged_sources"] = flagged_sources[:12]

    alias_candidates = quality_report.get("alias_candidates")
    if isinstance(alias_candidates, list) and alias_candidates:
        summary["quality_alias_candidates"] = alias_candidates[:12]

    match_review_items = quality_report.get("match_coverage_review_items")
    if isinstance(match_review_items, list) and match_review_items:
        summary["quality_match_coverage_review_items"] = match_review_items[:12]

    events = quality_report.get("events")
    if isinstance(events, list):
        alias_events = [
            row
            for row in events
            if isinstance(row, dict) and row.get("alias_traces")
        ]
        if alias_events:
            summary["quality_alias_events"] = alias_events[:12]

    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _summary_int(mapping: dict[str, Any], key: str) -> int:
    raw = mapping.get(key)
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int | float):
        return int(raw)
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return 0


def _quality_lookback_days(
    *,
    category_cfg: Any,
    quality_cfg: dict[str, Any],
    recent_days: int,
    minimum_days: int = 7,
) -> int:
    """Use source/event SLAs for quality even when report body is a smoke-sized window."""
    values = [recent_days, minimum_days]
    data_quality = quality_cfg.get("data_quality")
    if isinstance(data_quality, dict):
        values.extend(_freshness_sla_days(data_quality.get("freshness_sla")))

    for source in getattr(category_cfg, "sources", []):
        config = getattr(source, "config", {})
        if isinstance(config, dict):
            values.extend(_source_sla_days(config))

    return max(1, max(values))


def _freshness_sla_days(value: object) -> list[int]:
    days: list[int] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if key_text.endswith("_days") or key_text == "max_age_days":
                parsed = _positive_int(child)
                if parsed is not None:
                    days.append(parsed)
            elif key_text.endswith("_hours") or key_text == "max_age_hours":
                parsed = _positive_int(child)
                if parsed is not None:
                    days.append(max(1, ceil(parsed / 24)))
            days.extend(_freshness_sla_days(child))
    elif isinstance(value, list):
        for child in value:
            days.extend(_freshness_sla_days(child))
    return days


def _source_sla_days(config: dict[str, Any]) -> list[int]:
    days: list[int] = []
    parsed_days = _positive_int(config.get("freshness_sla_days"))
    if parsed_days is not None:
        days.append(parsed_days)
    parsed_hours = _positive_int(config.get("freshness_sla_hours"))
    if parsed_hours is not None:
        days.append(max(1, ceil(parsed_hours / 24)))
    return days


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return max(1, int(value))
    if isinstance(value, str):
        try:
            return max(1, int(float(value.strip())))
        except ValueError:
            return None
    return None


def run(
    *,
    category: str,
    config_path: Path | None = None,
    categories_dir: Path | None = None,
    per_source_limit: int = 30,
    recent_days: int = 7,
    timeout: int = 15,
    keep_days: int = 90,
    keep_raw_days: int = 180,
    keep_report_days: int = 90,
    snapshot_db: bool = False,
) -> Path:
    """Execute the lightweight collect -> analyze -> report pipeline."""
    configure_logging()
    settings = load_settings(config_path)
    raw_data_dir = getattr(settings, "raw_data_dir", settings.database_path.parent / "raw")
    category_cfg = load_category_config(category, categories_dir=categories_dir)
    quality_cfg = load_category_quality_config(category, categories_dir=categories_dir)

    logger.info(
        "pipeline_start",
        category=category_cfg.category_name,
        sources_count=len(category_cfg.sources),
    )
    collected, errors = collect_sources(
        category_cfg.sources,
        category=category_cfg.category_name,
        limit_per_source=per_source_limit,
        timeout=timeout,
    )
    collected = annotate_articles_with_ontology(
        collected,
        repo_name="FoodRadar",
        sources_by_name={source.name: source for source in category_cfg.sources},
        category_name=category_cfg.category_name,
        search_from=Path(__file__),
        attach_event_model_payload=True,
    )

    raw_logger = RawLogger(raw_data_dir)
    for source in category_cfg.sources:
        source_articles = [article for article in collected if article.source == source.name]
        if source_articles:
            _ = raw_logger.log(source_articles, source_name=source.name)

    data_quality = quality_cfg.get("data_quality")
    alias_map = data_quality.get("alias_map") if isinstance(data_quality, dict) else None
    analyzed = apply_entity_rules(
        collected,
        category_cfg.entities,
        alias_map=alias_map if isinstance(alias_map, dict) else None,
        sources=category_cfg.sources,
    )

    storage = RadarStorage(settings.database_path)
    storage.upsert_articles(analyzed)
    _ = storage.delete_older_than(keep_days)

    quality_days = _quality_lookback_days(
        category_cfg=category_cfg,
        quality_cfg=quality_cfg,
        recent_days=recent_days,
    )
    recent_articles = storage.recent_articles(category_cfg.category_name, days=recent_days)
    quality_articles = storage.recent_articles(
        category_cfg.category_name,
        days=quality_days,
        limit=1000,
    )
    storage.close()
    recent_articles = apply_entity_rules(
        recent_articles,
        category_cfg.entities,
        alias_map=alias_map if isinstance(alias_map, dict) else None,
        sources=category_cfg.sources,
    )
    quality_articles = apply_entity_rules(
        quality_articles,
        category_cfg.entities,
        alias_map=alias_map if isinstance(alias_map, dict) else None,
        sources=category_cfg.sources,
    )

    quality_report = build_quality_report(
        category=category_cfg,
        articles=quality_articles,
        errors=errors,
        quality_config=quality_cfg,
    )
    quality_report_paths = write_quality_report(
        quality_report,
        output_dir=settings.report_dir,
        category_name=category_cfg.category_name,
    )

    collected_matched_count = sum(1 for a in collected if a.matched_entities)
    matched_count = sum(1 for a in recent_articles if a.matched_entities)
    source_count = len({article.source for article in recent_articles if article.source})
    logger.info(
        "collection_complete",
        collected_count=len(collected),
        errors_count=len(errors),
    )
    logger.info(
        "analysis_complete",
        collected_matched_count=collected_matched_count,
        report_matched_count=matched_count,
    )

    stats = {
        "sources": len(category_cfg.sources),
        "collected": len(recent_articles),
        "matched": matched_count,
        "window_days": recent_days,
        "article_count": len(recent_articles),
        "source_count": source_count,
        "matched_count": matched_count,
    }

    output_path = settings.report_dir / f"{category_cfg.category_name}_report.html"
    _ = generate_report(
        category=category_cfg,
        articles=recent_articles,
        output_path=output_path,
        stats=stats,
        errors=errors,
        quality_report=quality_report,
    )
    summary_path = _latest_summary_path(settings.report_dir, category_cfg.category_name)
    if summary_path is not None:
        _augment_summary_with_quality(summary_path, quality_report)
    logger.info("report_generated", output_path=str(output_path))
    logger.info(
        "quality_report_generated",
        output_path=str(quality_report_paths["latest"]),
        stale_sources=quality_report["summary"]["stale_sources"],
        missing_sources=quality_report["summary"]["missing_sources"],
    )
    generate_index_html(settings.report_dir)
    if errors:
        logger.warning("collection_errors", errors_count=len(errors))

    date_storage = apply_date_storage_policy(
        database_path=settings.database_path,
        raw_data_dir=raw_data_dir,
        report_dir=settings.report_dir,
        keep_raw_days=keep_raw_days,
        keep_report_days=keep_report_days,
        snapshot_db=snapshot_db,
    )
    snapshot_path = date_storage.get("snapshot_path")
    if isinstance(snapshot_path, str) and snapshot_path:
        print(f"[Radar] Snapshot saved at {snapshot_path}")

    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FoodRadar - Korean food safety news collector")
    _ = parser.add_argument(
        "--category",
        required=True,
        help="Category name matching a YAML in config/categories/",
    )
    _ = parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config/config.yaml (optional)",
    )
    _ = parser.add_argument(
        "--categories-dir",
        type=Path,
        default=None,
        help="Custom directory for category YAML files",
    )
    _ = parser.add_argument(
        "--per-source-limit",
        type=int,
        default=30,
        help="Max items to pull from each source",
    )
    _ = parser.add_argument(
        "--recent-days", type=int, default=7, help="Window (days) to show in the report"
    )
    _ = parser.add_argument(
        "--timeout", type=int, default=15, help="HTTP timeout per request (seconds)"
    )
    _ = parser.add_argument(
        "--keep-days", type=int, default=90, help="Retention window for stored items"
    )
    _ = parser.add_argument(
        "--keep-raw-days", type=int, default=180, help="Retention window for raw JSONL directories"
    )
    _ = parser.add_argument(
        "--keep-report-days", type=int, default=90, help="Retention window for dated HTML reports"
    )
    _ = parser.add_argument(
        "--snapshot-db",
        action="store_true",
        default=False,
        help="Create a dated DuckDB snapshot after each run",
    )
    return parser.parse_args()


def _to_path(value: object) -> Path | None:
    if isinstance(value, Path):
        return value
    return None


def _to_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


if __name__ == "__main__":
    args = cast(dict[str, object], vars(parse_args()))
    _ = run(
        category=str(args.get("category", "")),
        config_path=_to_path(args.get("config")),
        categories_dir=_to_path(args.get("categories_dir")),
        per_source_limit=_to_int(args.get("per_source_limit"), 30),
        recent_days=_to_int(args.get("recent_days"), 7),
        timeout=_to_int(args.get("timeout"), 15),
        keep_days=_to_int(args.get("keep_days"), 90),
        keep_raw_days=_to_int(args.get("keep_raw_days"), 180),
        keep_report_days=_to_int(args.get("keep_report_days"), 90),
        snapshot_db=bool(args.get("snapshot_db", False)),
    )
