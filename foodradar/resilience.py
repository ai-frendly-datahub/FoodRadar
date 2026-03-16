from __future__ import annotations

import threading

import structlog
from pybreaker import CircuitBreaker, CircuitBreakerListener, CircuitBreakerState


logger = structlog.get_logger(__name__)


class SourceCircuitBreakerListener(CircuitBreakerListener):
    """Listener for circuit breaker state changes with structlog integration."""

    def state_change(
        self,
        cb: CircuitBreaker,
        old_state: CircuitBreakerState | None,
        new_state: CircuitBreakerState,
    ) -> None:
        """Log state transitions."""
        logger.info(
            "circuit_breaker_state_change",
            source=cb.name,
            before=old_state.name if old_state is not None else None,
            after=new_state.name,
        )

    def before_call(
        self, cb: CircuitBreaker, func: object, *args: object, **kwargs: object
    ) -> None:
        """Called before the circuit breaker executes a function."""

    def failure(
        self,
        cb: CircuitBreaker,
        exc: BaseException,
    ) -> None:
        """Log failures."""
        logger.warning(
            "circuit_breaker_failure",
            source=cb.name,
            exception=type(exc).__name__,
            message=str(exc),
        )

    def success(self, cb: CircuitBreaker) -> None:
        """Log successes."""
        logger.debug(
            "circuit_breaker_success",
            source=cb.name,
        )


class SourceCircuitBreakerManager:
    """Thread-safe registry of per-source circuit breakers."""

    def __init__(self) -> None:
        """Initialize the manager with empty registry and lock."""
        self._instances: dict[str, CircuitBreaker] = {}
        self._lock = threading.RLock()
        self._listener = SourceCircuitBreakerListener()

    def get_breaker(self, source_name: str) -> CircuitBreaker:
        """Get or create a circuit breaker for a source."""
        if source_name in self._instances:
            return self._instances[source_name]

        with self._lock:
            if source_name in self._instances:
                return self._instances[source_name]

            breaker = CircuitBreaker(
                fail_max=5,
                reset_timeout=60,
                success_threshold=2,
                listeners=[self._listener],
                name=source_name,
                exclude=[ValueError, KeyError, AttributeError],
            )
            self._instances[source_name] = breaker
            return breaker

    def reset_breaker(self, source_name: str) -> None:
        """Reset a specific source's circuit breaker."""
        with self._lock:
            if source_name in self._instances:
                self._instances[source_name].close()
                logger.info("circuit_breaker_reset", source=source_name)

    def reset_all(self) -> None:
        """Reset all circuit breakers."""
        with self._lock:
            for breaker in self._instances.values():
                breaker.close()
            logger.info("circuit_breaker_reset_all", count=len(self._instances))

    def get_status(self) -> dict[str, str]:
        """Get status of all circuit breakers."""
        with self._lock:
            return {
                name: breaker.current_state for name, breaker in self._instances.items()
            }


# Global singleton instance
_manager: SourceCircuitBreakerManager | None = None
_manager_lock = threading.Lock()


def get_circuit_breaker_manager() -> SourceCircuitBreakerManager:
    """Get or create the global circuit breaker manager."""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = SourceCircuitBreakerManager()
    return _manager
