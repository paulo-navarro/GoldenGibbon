"""
Retry utility with exponential backoff for executor calls.

Designed for use in BinanceExecutor (task 4.1) to handle transient
failures (network errors, rate limits, exchange 5xx) without retrying
on permanent errors (insufficient funds, invalid symbol, bad request).

Usage:
    from core.execution.retry import RetryConfig, with_retry

    config = RetryConfig(max_attempts=3, base_delay=1.0, max_delay=30.0)

    result = with_retry(
        fn=lambda: client.place_order(symbol, side, qty),
        config=config,
        retryable=(NetworkError, RateLimitError),
    )
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple, Type, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Errors that should never be retried regardless of RetryConfig
PERMANENT_ERRORS: Tuple[Type[Exception], ...] = (
    ValueError,       # Bad arguments, invalid symbol, malformed request
    TypeError,        # Programming errors
    KeyboardInterrupt,
    SystemExit,
)


class RetryExhausted(Exception):
    """Raised when all retry attempts fail."""

    def __init__(self, attempts: int, last_error: Exception) -> None:
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"Failed after {attempts} attempt(s). "
            f"Last error: {type(last_error).__name__}: {last_error}"
        )


@dataclass
class RetryConfig:
    """
    Configuration for exponential-backoff retry behaviour.

    Delay formula:  min(base_delay * (backoff_factor ** attempt), max_delay)
    Optional jitter adds a random fraction to avoid thundering herd.

    Attributes:
        max_attempts:   Total number of attempts (1 = no retry).
        base_delay:     Initial delay in seconds before the first retry.
        max_delay:      Upper cap on delay between retries.
        backoff_factor: Multiplier applied to delay on each retry.
        jitter:         If True, adds uniform random jitter up to 50% of delay.
        retryable_exceptions: Tuple of exception types that trigger a retry.
                              Empty tuple → retry on any non-permanent exception.
    """

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    backoff_factor: float = 2.0
    jitter: bool = True
    retryable_exceptions: Tuple[Type[Exception], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay < 0:
            raise ValueError("base_delay must be >= 0")
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay must be >= base_delay")
        if self.backoff_factor < 1.0:
            raise ValueError("backoff_factor must be >= 1.0")

    def delay_for(self, attempt: int) -> float:
        """
        Return the sleep duration (seconds) before the given attempt number.

        Args:
            attempt: 0-based retry count (0 = first retry after initial failure).

        Returns:
            Seconds to sleep (0 before the first attempt).
        """
        import random

        delay = min(self.base_delay * (self.backoff_factor ** attempt), self.max_delay)
        if self.jitter:
            delay *= 1 + random.uniform(0, 0.5)
        return delay

    def is_retryable(self, exc: Exception) -> bool:
        """
        Return True if *exc* should trigger a retry.

        Never retries PERMANENT_ERRORS regardless of configuration.
        If retryable_exceptions is empty, retries on any other exception.
        """
        if isinstance(exc, PERMANENT_ERRORS):
            return False
        if not self.retryable_exceptions:
            return True
        return isinstance(exc, self.retryable_exceptions)


def with_retry(
    fn: Callable[[], T],
    config: Optional[RetryConfig] = None,
    retryable: Optional[Tuple[Type[Exception], ...]] = None,
    label: str = "operation",
) -> T:
    """
    Call *fn* with exponential-backoff retry on transient failures.

    Args:
        fn:        Zero-argument callable to attempt.
        config:    RetryConfig instance. Defaults to RetryConfig() if omitted.
        retryable: Override for config.retryable_exceptions (convenience param).
        label:     Human-readable name used in log messages.

    Returns:
        The return value of *fn* on success.

    Raises:
        RetryExhausted: If all attempts fail.
        Exception:      Immediately re-raises permanent (non-retryable) errors.
    """
    cfg = config or RetryConfig()
    if retryable is not None:
        cfg = RetryConfig(
            max_attempts=cfg.max_attempts,
            base_delay=cfg.base_delay,
            max_delay=cfg.max_delay,
            backoff_factor=cfg.backoff_factor,
            jitter=cfg.jitter,
            retryable_exceptions=retryable,
        )

    last_exc: Optional[Exception] = None

    for attempt in range(cfg.max_attempts):
        try:
            return fn()
        except Exception as exc:
            if not cfg.is_retryable(exc):
                logger.error(
                    "%s failed with non-retryable error: %s: %s",
                    label, type(exc).__name__, exc,
                )
                raise

            last_exc = exc
            remaining = cfg.max_attempts - attempt - 1

            if remaining == 0:
                break

            delay = cfg.delay_for(attempt)
            logger.warning(
                "%s failed (attempt %d/%d): %s: %s — retrying in %.2fs",
                label, attempt + 1, cfg.max_attempts,
                type(exc).__name__, exc, delay,
            )
            time.sleep(delay)

    raise RetryExhausted(attempts=cfg.max_attempts, last_error=last_exc)
