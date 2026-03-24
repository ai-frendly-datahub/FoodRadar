from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from foodradar.analyzer import apply_entity_rules
from foodradar.collector import collect_sources
from foodradar.config_loader import load_category_config, load_settings
from radar_core.date_storage import apply_date_storage_policy
from foodradar.logger import configure_logging, get_logger
from foodradar.reporter import generate_index_html, generate_report
from foodradar.storage import RadarStorage


logger = get_logger(__name__)


def run(
    *,
    category: str,
    config_path: Path | None = None,
    categories_dir: Path | None = None,
    per_source_limit: int = 30,
    recent_days: int = 7,
    timeout: int = 15,
    keep_days: int = 90,
    snapshot_db: bool = False,
) -> Path:
    """Execute the lightweight collect -> analyze -> report pipeline."""
    configure_logging()
    settings = load_settings(config_path)
    category_cfg = load_category_config(category, categories_dir=categories_dir)

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

    analyzed = apply_entity_rules(collected, category_cfg.entities)

    storage = RadarStorage(settings.database_path)
    storage.upsert_articles(analyzed)
    _ = storage.delete_older_than(keep_days)

    recent_articles = storage.recent_articles(category_cfg.category_name, days=recent_days)
    storage.close()

    matched_count = sum(1 for a in collected if a.matched_entities)
    logger.info(
        "collection_complete",
        collected_count=len(collected),
        errors_count=len(errors),
    )
    logger.info("analysis_complete", matched_count=matched_count)

    stats = {
        "sources": len(category_cfg.sources),
        "collected": len(collected),
        "matched": matched_count,
        "window_days": recent_days,
    }

    output_path = settings.report_dir / f"{category_cfg.category_name}_report.html"
    _ = generate_report(
        category=category_cfg,
        articles=recent_articles,
        output_path=output_path,
        stats=stats,
        errors=errors,
    )
    logger.info("report_generated", output_path=str(output_path))
    generate_index_html(settings.report_dir)
    if errors:
        logger.warning("collection_errors", errors_count=len(errors))

    raw_data_dir = getattr(settings, "raw_data_dir", settings.database_path.parent / "raw")
    keep_raw_days = getattr(settings, "keep_raw_days", 180)
    keep_report_days = getattr(settings, "keep_report_days", 90)
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
        snapshot_db=bool(args.get("snapshot_db", False)),
    )
