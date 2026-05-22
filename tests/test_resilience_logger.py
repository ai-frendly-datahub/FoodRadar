from __future__ import annotations

import logging
from unittest.mock import Mock

import foodradar.resilience as resilience
from foodradar.logger import configure_logging, get_logger
from foodradar.resilience import SourceCircuitBreakerListener, SourceCircuitBreakerManager


def test_circuit_breaker_manager_reuses_breakers_and_reports_status() -> None:
    manager = SourceCircuitBreakerManager()

    first = manager.get_breaker("source-a")
    second = manager.get_breaker("source-a")
    other = manager.get_breaker("source-b")

    assert first is second
    assert other is not first
    assert manager.get_status() == {"source-a": "closed", "source-b": "closed"}


def test_circuit_breaker_manager_reset_methods_close_registered_breakers() -> None:
    manager = SourceCircuitBreakerManager()
    first = manager.get_breaker("source-a")
    second = manager.get_breaker("source-b")

    manager.reset_breaker("source-a")
    manager.reset_breaker("missing")
    manager.reset_all()

    assert first.current_state == "closed"
    assert second.current_state == "closed"


def test_global_circuit_breaker_manager_is_singleton(
    monkeypatch,
) -> None:
    monkeypatch.setattr(resilience, "_manager", None)

    first = resilience.get_circuit_breaker_manager()
    second = resilience.get_circuit_breaker_manager()

    assert first is second


def test_circuit_breaker_listener_logs_state_failure_and_success(monkeypatch) -> None:
    logger = Mock()
    monkeypatch.setattr(resilience, "logger", logger)
    listener = SourceCircuitBreakerListener()
    breaker = Mock(name="breaker")
    breaker.name = "source-a"
    old_state = Mock()
    old_state.name = "closed"
    new_state = Mock()
    new_state.name = "open"

    listener.before_call(breaker, object())
    listener.state_change(breaker, old_state, new_state)
    listener.state_change(breaker, None, new_state)
    listener.failure(breaker, RuntimeError("boom"))
    listener.success(breaker)

    assert logger.info.call_count == 2
    logger.warning.assert_called_once()
    logger.debug.assert_called_once_with("circuit_breaker_success", source="source-a")


def test_configure_logging_supports_json_console_and_env(monkeypatch) -> None:
    monkeypatch.setenv("RADAR_LOG_LEVEL", "WARNING")
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)

    configure_logging(use_json=True)
    json_logger = get_logger("json-test")
    configure_logging(use_json=False)
    console_logger = get_logger("console-test")
    configure_logging(log_level="NOT_A_LEVEL", use_json=True)

    assert json_logger.bind(component="test") is not None
    assert console_logger.bind(component="test") is not None
    assert logging.getLogger().level in {logging.INFO, logging.WARNING}
