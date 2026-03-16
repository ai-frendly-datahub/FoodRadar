from __future__ import annotations

import os
import sys
from typing import Any

import structlog


def configure_logging(
    log_level: str | None = None,
    use_json: bool | None = None,
) -> None:
    """Configure structlog with JSON or console output.

    Args:
        log_level: Log level (DEBUG, INFO, WARNING, ERROR). Defaults to RADAR_LOG_LEVEL env var or INFO.
        use_json: Use JSON output (True) or console renderer (False). Auto-detect if None.
    """
    if log_level is None:
        log_level = os.environ.get("RADAR_LOG_LEVEL", "INFO").upper()

    if use_json is None:
        use_json = not sys.stderr.isatty()

    processors: list[Any] = [
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if use_json:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(
            structlog.dev.ConsoleRenderer(
                colors=True,
                exception_formatter=structlog.dev.rich_traceback,
            )
        )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    logging_level = getattr(__import__("logging"), log_level, "INFO")
    __import__("logging").basicConfig(level=logging_level)


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a structured logger instance.

    Args:
        name: Logger name (typically __name__ from calling module).

    Returns:
        A BoundLogger configured for structured logging.
    """
    return structlog.get_logger(name)
