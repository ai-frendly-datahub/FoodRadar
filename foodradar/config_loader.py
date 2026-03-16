from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml

from radar_core.models import (
    CategoryConfig,
    EntityDefinition,
    NotificationConfig,
    RadarSettings,
    Source,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read_yaml(path: Path) -> dict[str, object]:
    raw = cast(object, yaml.safe_load(path.read_text(encoding="utf-8")))
    if isinstance(raw, dict):
        return {str(k): v for k, v in cast(dict[object, object], raw).items()}
    return {}


def _str(d: dict[str, object], k: str, default: str = "") -> str:
    v = d.get(k)
    return v if isinstance(v, str) and v.strip() else default


def _path(val: str) -> Path:
    p = Path(val).expanduser()
    return p if p.is_absolute() else (_PROJECT_ROOT / p).resolve()


def load_settings(config_path: Path | None = None) -> RadarSettings:
    f = config_path or _PROJECT_ROOT / "config" / "config.yaml"
    if not f.exists():
        raise FileNotFoundError(f"Config file not found: {f}")
    raw = _read_yaml(f)
    return RadarSettings(
        database_path=_path(_str(raw, "database_path", "data/radar_data.duckdb")),
        report_dir=_path(_str(raw, "report_dir", "reports")),
        raw_data_dir=_path(_str(raw, "raw_data_dir", "data/raw")),
        search_db_path=_path(_str(raw, "search_db_path", "data/search_index.db")),
    )


def load_category_config(
    category_name: str, categories_dir: Path | None = None
) -> CategoryConfig:
    base = categories_dir or _PROJECT_ROOT / "config" / "categories"
    f = Path(base) / f"{category_name}.yaml"
    if not f.exists():
        raise FileNotFoundError(f"Category config not found: {f}")
    raw = _read_yaml(f)
    sources = []
    for s in (raw.get("sources") or []):
        if isinstance(s, dict):
            sd = {str(k): v for k, v in cast(dict[object, object], s).items()}
            sources.append(Source(name=_str(sd, "name", "Unnamed"), type=_str(sd, "type", "rss"), url=_str(sd, "url")))
    entities = []
    for e in (raw.get("entities") or []):
        if isinstance(e, dict):
            ed = {str(k): v for k, v in cast(dict[object, object], e).items()}
            kw_raw = ed.get("keywords", [])
            kws = [str(k).strip() for k in (kw_raw if isinstance(kw_raw, list) else []) if str(k).strip()]
            entities.append(EntityDefinition(name=_str(ed, "name", "entity"), display_name=_str(ed, "display_name", _str(ed, "name", "entity")), keywords=kws))
    dn = _str(raw, "display_name") or _str(raw, "category_name") or category_name
    return CategoryConfig(category_name=_str(raw, "category_name", category_name), display_name=dn, sources=sources, entities=entities)


def load_notification_config(config_path: Path | None = None) -> NotificationConfig:
    f = config_path or _PROJECT_ROOT / "config" / "notifications.yaml"
    if not f.exists():
        return NotificationConfig(enabled=False, channels=[])
    return NotificationConfig(enabled=False, channels=[])


__all__ = ["load_category_config", "load_notification_config", "load_settings"]

