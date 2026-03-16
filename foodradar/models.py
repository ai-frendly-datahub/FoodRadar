from __future__ import annotations

# Re-export from radar-core shared package
from radar_core.models import (
    EmailConfig,
    Article,
    CategoryConfig,
    EmailSettings,
    EntityDefinition,
    RadarSettings,
    Source,
    TelegramSettings,
    StandardNotificationConfig,
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
