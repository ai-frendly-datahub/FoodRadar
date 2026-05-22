from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from foodradar.config_loader import (
    load_category_config,
    load_category_quality_config,
    load_notification_config,
    load_settings,
    source_language_overrides,
)


def _write_yaml(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_load_settings_resolves_relative_paths(tmp_path: Path) -> None:
    config_path = _write_yaml(
        tmp_path / "config.yaml",
        {
            "database_path": "tmp/radar.duckdb",
            "report_dir": "tmp/reports",
            "raw_data_dir": "tmp/raw",
            "search_db_path": "tmp/search.db",
        },
    )

    settings = load_settings(config_path)

    assert settings.database_path.is_absolute()
    assert settings.database_path.name == "radar.duckdb"
    assert settings.report_dir.name == "reports"
    assert settings.raw_data_dir.name == "raw"
    assert settings.search_db_path.name == "search.db"


def test_load_settings_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "missing.yaml")


def test_load_category_config_coerces_optional_source_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOKEN", "secret")
    categories_dir = tmp_path / "categories"
    _write_yaml(
        categories_dir / "food.yaml",
        {
            "category_name": "food",
            "display_name": "",
            "sources": [
                {
                    "name": "Optional RSS",
                    "type": "rss",
                    "url": "https://example.com/feed",
                    "enabled": "no",
                    "weight": "2.5",
                    "info_purpose": "recall_event",
                    "config": {
                        "headers": {"Authorization": "Bearer ${TOKEN}"},
                        "flags": ["${TOKEN}", 3],
                    },
                },
                "ignored",
            ],
            "entities": [
                {"name": "FoodType", "keywords": [" rice ", "", 42]},
                {"name": "IgnoredKeywords", "keywords": "not-a-list"},
            ],
        },
    )

    category = load_category_config("food", categories_dir=categories_dir)

    assert category.display_name == "food"
    assert len(category.sources) == 1
    source = category.sources[0]
    assert source.enabled is False
    assert source.weight == 2.5
    assert source.info_purpose == ["recall_event"]
    assert source.config["headers"]["Authorization"] == "Bearer secret"
    assert source.config["flags"] == ["secret", 3]
    assert category.entities[0].keywords == ["rice", "42"]
    assert category.entities[1].keywords == []


def test_load_category_quality_config_returns_only_quality_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_KEY", "abc")
    categories_dir = tmp_path / "categories"
    _write_yaml(
        categories_dir / "food.yaml",
        {
            "category_name": "food",
            "sources": [],
            "entities": [],
            "data_quality": {"token": "${API_KEY}"},
            "source_backlog": {"items": ["${API_KEY}"]},
            "integration_candidates": {"candidate": {"env": "${API_KEY}"}},
            "unrelated": "ignored",
        },
    )

    quality = load_category_quality_config("food", categories_dir=categories_dir)

    assert set(quality) == {
        "data_quality",
        "source_backlog",
        "integration_candidates",
    }
    assert quality["data_quality"] == {"token": "abc"}
    assert quality["source_backlog"] == {"items": ["abc"]}
    assert quality["integration_candidates"] == {"candidate": {"env": "abc"}}


def test_source_language_overrides_reads_legacy_metadata() -> None:
    overrides = source_language_overrides(
        {
            "data_quality": {
                "legacy_source_languages": {
                    "식품저널": "ko",
                    " ": "ignored",
                    "Food Archive": " en ",
                }
            }
        }
    )

    assert overrides == {"식품저널": "ko", "Food Archive": "en"}


def test_load_category_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_category_config("food", categories_dir=tmp_path)


def test_load_notification_config_defaults_disabled(tmp_path: Path) -> None:
    assert load_notification_config(tmp_path / "missing.yaml").enabled is False

    config_path = _write_yaml(tmp_path / "notifications.yaml", {"enabled": True})
    loaded = load_notification_config(config_path)

    assert loaded.enabled is False
    assert loaded.channels == []
