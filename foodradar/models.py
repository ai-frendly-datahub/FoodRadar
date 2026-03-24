from __future__ import annotations

# Re-export from radar-core shared package
from radar_core.models import (
    Article,
    CategoryConfig,
    EmailConfig,
    EmailSettings,
    EntityDefinition,
    RadarSettings,
    Source,
    StandardNotificationConfig,
    TelegramSettings,
    WebhookConfig,
)


__all__ = [
    "Article",
    "CategoryConfig",
    "EmailSettings",
    "EntityDefinition",
    "NotificationConfig",
    "RadarSettings",
    "Source",
    "EmailConfig",
    "StandardNotificationConfig",
    "TelegramSettings",
    "WebhookConfig",
]

NotificationConfig = StandardNotificationConfig
