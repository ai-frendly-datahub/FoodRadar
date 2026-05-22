from __future__ import annotations

import os
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


def _bool(d: dict[str, object], k: str, default: bool) -> bool:
    v = d.get(k)
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        lowered = v.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
    return default


def _float(d: dict[str, object], k: str, default: float) -> float:
    v = d.get(k)
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return default
    return default


def _strings(d: dict[str, object], k: str) -> list[str]:
    v = d.get(k)
    if isinstance(v, list):
        values = cast(list[object], v)
    elif isinstance(v, tuple | set):
        values = list(cast(tuple[object, ...] | set[object], v))
    elif isinstance(v, str) and v.strip():
        values = [v]
    else:
        values = []
    return [str(item).strip() for item in values if str(item).strip()]


def _dict_value(d: dict[str, object], k: str) -> dict[str, object]:
    v = d.get(k)
    if isinstance(v, dict):
        return {
            str(key): _resolve_env_refs(value)
            for key, value in cast(dict[object, object], v).items()
        }
    return {}


def _path(val: str) -> Path:
    p = Path(val).expanduser()
    return p if p.is_absolute() else (_PROJECT_ROOT / p).resolve()


def _resolve_env_refs(value: object) -> object:
    if isinstance(value, str):
        result = value
        import re

        for match in re.finditer(r"\$\{([^}]+)\}", value):
            var_name = match.group(1)
            env_value = os.environ.get(var_name, "")
            result = result.replace(match.group(0), env_value)
        return result
    if isinstance(value, dict):
        return {k: _resolve_env_refs(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_refs(item) for item in value]
    return value


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


def _category_file(category_name: str, categories_dir: Path | None = None) -> Path:
    base = categories_dir or _PROJECT_ROOT / "config" / "categories"
    f = Path(base) / f"{category_name}.yaml"
    if not f.exists():
        raise FileNotFoundError(f"Category config not found: {f}")
    return f


def load_category_config(category_name: str, categories_dir: Path | None = None) -> CategoryConfig:
    raw = _read_yaml(_category_file(category_name, categories_dir))
    sources = []
    for s in raw.get("sources") or []:
        if isinstance(s, dict):
            sd = cast(
                dict[str, object],
                _resolve_env_refs(
                    {str(k): v for k, v in cast(dict[object, object], s).items()}
                ),
            )
            sources.append(
                Source(
                    name=_str(sd, "name", "Unnamed"),
                    type=_str(sd, "type", "rss"),
                    url=_str(sd, "url"),
                    id=_str(sd, "id", ""),
                    enabled=_bool(sd, "enabled", True),
                    language=_str(sd, "language", ""),
                    country=_str(sd, "country", ""),
                    region=_str(sd, "region", ""),
                    trust_tier=_str(sd, "trust_tier", "T3_professional"),
                    weight=_float(sd, "weight", 1.0),
                    content_type=_str(sd, "content_type", "news"),
                    collection_tier=_str(sd, "collection_tier", "C1_rss"),
                    producer_role=_str(sd, "producer_role", ""),
                    info_purpose=_strings(sd, "info_purpose"),
                    notes=_str(sd, "notes", ""),
                    config=_dict_value(sd, "config"),
                )
            )
    entities = []
    for e in raw.get("entities") or []:
        if isinstance(e, dict):
            ed = {str(k): v for k, v in cast(dict[object, object], e).items()}
            kw_raw = ed.get("keywords", [])
            kws = [
                str(k).strip()
                for k in (kw_raw if isinstance(kw_raw, list) else [])
                if str(k).strip()
            ]
            entities.append(
                EntityDefinition(
                    name=_str(ed, "name", "entity"),
                    display_name=_str(ed, "display_name", _str(ed, "name", "entity")),
                    keywords=kws,
                )
            )
    dn = _str(raw, "display_name") or _str(raw, "category_name") or category_name
    return CategoryConfig(
        category_name=_str(raw, "category_name", category_name),
        display_name=dn,
        sources=sources,
        entities=entities,
    )


def load_category_quality_config(
    category_name: str, categories_dir: Path | None = None
) -> dict[str, object]:
    raw = _read_yaml(_category_file(category_name, categories_dir))
    quality_config: dict[str, object] = {}
    for key in ("data_quality", "source_backlog", "integration_candidates"):
        if key in raw:
            quality_config[key] = _resolve_env_refs(raw[key])
    return quality_config


def source_language_overrides(quality_config: dict[str, object]) -> dict[str, str]:
    data_quality = quality_config.get("data_quality")
    if not isinstance(data_quality, dict):
        return {}
    raw_languages = data_quality.get("legacy_source_languages")
    if not isinstance(raw_languages, dict):
        return {}

    languages: dict[str, str] = {}
    for source_name, language in cast(dict[object, object], raw_languages).items():
        clean_source_name = str(source_name).strip()
        clean_language = str(language).strip()
        if clean_source_name and clean_language:
            languages[clean_source_name] = clean_language
    return languages


def load_notification_config(config_path: Path | None = None) -> NotificationConfig:
    f = config_path or _PROJECT_ROOT / "config" / "notifications.yaml"
    if not f.exists():
        return NotificationConfig(enabled=False, channels=[])
    return NotificationConfig(enabled=False, channels=[])


__all__ = [
    "load_category_config",
    "load_category_quality_config",
    "load_notification_config",
    "load_settings",
    "source_language_overrides",
]
