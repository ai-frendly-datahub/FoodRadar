from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml

from .models import (
    CategoryConfig,
    EmailConfig,
    EmailSettings,
    EntityDefinition,
    NotificationConfig,
    RadarSettings,
    Source,
    StandardNotificationConfig,
    TelegramSettings,
    WebhookConfig,
)


def _resolve_path(path_value: str, *, project_root: Path) -> Path:
    """Resolve a path from config, treating relative paths as project-root relative."""
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return (project_root / path).resolve()


def _read_yaml_dict(path: Path) -> dict[str, object]:
    raw = cast(object, yaml.safe_load(path.read_text(encoding="utf-8")))
    if isinstance(raw, dict):
        raw_dict = cast(dict[object, object], raw)
        return {str(k): v for k, v in raw_dict.items()}
    return {}


def _string_value(raw: dict[str, object], key: str, default: str) -> str:
    value = raw.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return default


def _dict_items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []

    items: list[dict[str, object]] = []
    for item in cast(list[object], value):
        if isinstance(item, dict):
            item_dict = cast(dict[object, object], item)
            items.append({str(k): v for k, v in item_dict.items()})
    return items


def _resolve_env_var(value: str) -> str:
    """Resolve environment variable references like ${VAR_NAME}."""
    import os
    import re

    def replace_var(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, "")

    return re.sub(r"\$\{([^}]+)\}", replace_var, value)


def _resolve_env_refs(value: object) -> object:
    """Resolve ${VAR} environment variable references in strings."""
    if isinstance(value, str):
        result = value
        import re

        for match in re.finditer(r"\$\{([^}]+)\}", value):
            var_name = match.group(1)
            env_value = __import__("os").environ.get(var_name, "")
            result = result.replace(match.group(0), env_value)
        return result
    elif isinstance(value, dict):
        return {k: _resolve_env_refs(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_refs(item) for item in value]
    return value


def load_settings(config_path: Path | None = None) -> RadarSettings:
    """Load global radar settings such as database and report directories."""
    project_root = Path(__file__).resolve().parent.parent
    config_file = config_path or project_root / "config" / "config.yaml"

    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file}")

    raw = _read_yaml_dict(config_file)
    db_path = _resolve_path(
        _string_value(raw, "database_path", "data/radar_data.duckdb"),
        project_root=project_root,
    )
    report_dir = _resolve_path(
        _string_value(raw, "report_dir", "reports"), project_root=project_root
    )
    raw_data_dir = _resolve_path(
        _string_value(raw, "raw_data_dir", "data/raw"), project_root=project_root
    )
    search_db_path = _resolve_path(
        _string_value(raw, "search_db_path", "data/search_index.db"),
        project_root=project_root,
    )

    notifications = None
    notif_raw = raw.get("notifications")
    if isinstance(notif_raw, dict):
        notif_dict = cast(dict[object, object], notif_raw)
        notifications = _parse_notifications({str(k): v for k, v in notif_dict.items()})

    return RadarSettings(
        database_path=db_path,
        report_dir=report_dir,
        raw_data_dir=raw_data_dir,
        search_db_path=search_db_path,
        notifications=notifications,
    )


def load_category_config(
    category_name: str, categories_dir: Path | None = None
) -> CategoryConfig:
    """Load a category YAML and parse it into a CategoryConfig object."""
    project_root = Path(__file__).resolve().parent.parent
    base_dir = categories_dir or project_root / "config" / "categories"
    config_file = Path(base_dir) / f"{category_name}.yaml"

    if not config_file.exists():
        raise FileNotFoundError(f"Category config not found: {config_file}")

    raw = _read_yaml_dict(config_file)
    sources = [_parse_source(entry) for entry in _dict_items(raw.get("sources"))]
    entities = [_parse_entity(entry) for entry in _dict_items(raw.get("entities"))]

    display_name = (
        _string_value(raw, "display_name", "")
        or _string_value(raw, "category_name", "")
        or category_name
    )

    return CategoryConfig(
        category_name=_string_value(raw, "category_name", category_name),
        display_name=display_name,
        sources=sources,
        entities=entities,
    )


def _parse_source(entry: dict[str, object]) -> Source:
    if not entry:
        raise ValueError("Empty source entry in category config")
    return Source(
        name=_string_value(entry, "name", "Unnamed Source"),
        type=_string_value(entry, "type", "rss"),
        url=_string_value(entry, "url", ""),
    )


def _parse_entity(entry: dict[str, object]) -> EntityDefinition:
    if not entry:
        raise ValueError("Empty entity entry in category config")
    name = _string_value(entry, "name", "entity")
    display_name = _string_value(entry, "display_name", name)
    keywords_raw = entry.get("keywords")
    keywords: list[object]
    if isinstance(keywords_raw, list):
        keywords = []
        for keyword in cast(list[object], keywords_raw):
            keywords.append(keyword)
    elif isinstance(keywords_raw, tuple | set):
        keywords = []
        for keyword in cast(tuple[object, ...] | set[object], keywords_raw):
            keywords.append(keyword)
    else:
        keywords = []
    keyword_list = [
        str(keyword).strip() for keyword in keywords if str(keyword).strip()
    ]
    return EntityDefinition(name=name, display_name=display_name, keywords=keyword_list)


def _parse_notifications(raw: dict[str, object]) -> NotificationConfig:
    """Parse notification configuration from YAML."""
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        enabled = False

    email_raw = raw.get("email")
    email_dict: dict[str, object] = {}
    if isinstance(email_raw, dict):
        email_raw_dict = cast(dict[object, object], email_raw)
        email_dict = {str(k): v for k, v in email_raw_dict.items()}

    email_enabled = email_dict.get("enabled", False)
    if not isinstance(email_enabled, bool):
        email_enabled = False

    email_smtp_password = _string_value(email_dict, "smtp_password", "")
    email_smtp_password = _resolve_env_var(email_smtp_password)

    email_to_addrs_raw = email_dict.get("to_addrs")
    email_to_addrs: list[str] = []
    if isinstance(email_to_addrs_raw, list):
        email_to_addrs = [
            str(addr).strip()
            for addr in cast(list[object], email_to_addrs_raw)
            if str(addr).strip()
        ]

    smtp_port_raw = email_dict.get("smtp_port", 587)
    smtp_port = 587
    if isinstance(smtp_port_raw, int):
        smtp_port = smtp_port_raw

    email_config = EmailConfig(
        enabled=email_enabled,
        smtp_host=_string_value(email_dict, "smtp_host", ""),
        smtp_port=smtp_port,
        smtp_user=_string_value(email_dict, "smtp_user", ""),
        smtp_password=email_smtp_password,
        from_addr=_string_value(email_dict, "from_addr", ""),
        to_addrs=email_to_addrs,
    )

    webhook_raw = raw.get("webhook")
    webhook_dict: dict[str, object] = {}
    if isinstance(webhook_raw, dict):
        webhook_raw_dict = cast(dict[object, object], webhook_raw)
        webhook_dict = {str(k): v for k, v in webhook_raw_dict.items()}

    webhook_enabled = webhook_dict.get("enabled", False)
    if not isinstance(webhook_enabled, bool):
        webhook_enabled = False

    webhook_headers_raw = webhook_dict.get("headers")
    webhook_headers: dict[str, str] = {}
    if isinstance(webhook_headers_raw, dict):
        headers_dict = cast(dict[object, object], webhook_headers_raw)
        webhook_headers = {str(k): str(v) for k, v in headers_dict.items()}

    webhook_config = WebhookConfig(
        enabled=webhook_enabled,
        url=_string_value(webhook_dict, "url", ""),
        method=_string_value(webhook_dict, "method", "POST"),
        headers=webhook_headers,
    )

    return NotificationConfig(
        enabled=enabled,
        email=email_config,
        webhook=webhook_config,
    )


def load_notification_config(
    config_path: Path | None = None,
) -> StandardNotificationConfig:
    """Load notification configuration from notifications.yaml."""
    project_root = Path(__file__).resolve().parent.parent
    config_file = config_path or project_root / "config" / "notifications.yaml"

    if not config_file.exists():
        return StandardNotificationConfig(enabled=False, channels=[])

    raw = _read_yaml_dict(config_file)
    notifications_raw = raw.get("notifications", {})
    if not isinstance(notifications_raw, dict):
        return StandardNotificationConfig(enabled=False, channels=[])

    notifications_dict = cast(dict[str, object], notifications_raw)
    enabled = bool(notifications_dict.get("enabled", False))
    channels_raw = notifications_dict.get("channels", [])
    channels = [str(c) for c in cast(list[object], channels_raw) if isinstance(c, str)]

    email_settings = None
    email_raw = notifications_dict.get("email")
    if isinstance(email_raw, dict):
        email_dict = cast(dict[str, object], _resolve_env_refs(email_raw))
        try:
            smtp_port_raw = email_dict.get("smtp_port", 587)
            smtp_port = (
                int(smtp_port_raw) if isinstance(smtp_port_raw, (int, str)) else 587
            )
            email_settings = EmailSettings(
                smtp_host=_string_value(email_dict, "smtp_host", ""),
                smtp_port=smtp_port,
                username=_string_value(email_dict, "username", ""),
                password=_string_value(email_dict, "password", ""),
                from_address=_string_value(email_dict, "from_address", ""),
                to_addresses=[
                    str(addr)
                    for addr in cast(list[object], email_dict.get("to_addresses", []))
                    if isinstance(addr, str)
                ],
            )
        except (ValueError, KeyError):
            email_settings = None

    webhook_url = None
    webhook_raw = notifications_dict.get("webhook_url")
    if isinstance(webhook_raw, str):
        resolved = _resolve_env_refs(webhook_raw)
        webhook_url = str(resolved) if resolved else None

    telegram_settings = None
    telegram_raw = notifications_dict.get("telegram")
    if isinstance(telegram_raw, dict):
        telegram_dict = cast(dict[str, object], _resolve_env_refs(telegram_raw))
        try:
            telegram_settings = TelegramSettings(
                bot_token=_string_value(telegram_dict, "bot_token", ""),
                chat_id=_string_value(telegram_dict, "chat_id", ""),
            )
        except (ValueError, KeyError):
            telegram_settings = None

    rules_raw = notifications_dict.get("rules", {})
    rules = (
        cast(dict[str, object], _resolve_env_refs(rules_raw))
        if isinstance(rules_raw, dict)
        else {}
    )

    return StandardNotificationConfig(
        enabled=enabled,
        channels=channels,
        email=email_settings,
        webhook_url=webhook_url,
        telegram=telegram_settings,
        rules=rules,
    )
