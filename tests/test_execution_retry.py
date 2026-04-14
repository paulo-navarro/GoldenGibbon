"""
Unit tests for core/execution/retry.py

Tests cover:
  - RetryConfig validation and delay calculation
  - with_retry success path
  - with_retry retries on transient errors and raises RetryExhausted
  - with_retry re-raises permanent errors immediately (no retry)
  - Correct number of calls and delays
  - retryable kwarg override
  - jitter behaviour
"""

from unittest.mock import MagicMock, call, patch

import pytest

from core.execution.retry import (
    PERMANENT_ERRORS,
    RetryConfig,
    RetryExhausted,
    with_retry,
)


# ── RetryConfig ───────────────────────────────────────────────────────────────

class TestRetryConfig:
    def test_defaults(self):
        cfg = RetryConfig()
        assert cfg.max_attempts == 3
        assert cfg.base_delay == 1.0
        assert cfg.max_delay == 60.0
        assert cfg.backoff_factor == 2.0
        assert cfg.jitter is True
        assert cfg.retryable_exceptions == tuple()

    def test_invalid_max_attempts(self):
        with pytest.raises(ValueError, match="max_attempts"):
            RetryConfig(max_attempts=0)

    def test_invalid_base_delay(self):
        with pytest.raises(ValueError, match="base_delay"):
            RetryConfig(base_delay=-1)

    def test_invalid_max_delay_less_than_base(self):
        with pytest.raises(ValueError, match="max_delay"):
            RetryConfig(base_delay=10.0, max_delay=5.0)

    def test_invalid_backoff_factor(self):
        with pytest.raises(ValueError, match="backoff_factor"):
            RetryConfig(backoff_factor=0.5)

    def test_delay_for_attempt_0(self):
        cfg = RetryConfig(base_delay=1.0, backoff_factor=2.0, jitter=False)
        assert cfg.delay_for(0) == 1.0

    def test_delay_for_attempt_1(self):
        cfg = RetryConfig(base_delay=1.0, backoff_factor=2.0, jitter=False)
        assert cfg.delay_for(1) == 2.0

    def test_delay_for_attempt_2(self):
        cfg = RetryConfig(base_delay=1.0, backoff_factor=2.0, jitter=False)
        assert cfg.delay_for(2) == 4.0

    def test_delay_capped_at_max_delay(self):
        cfg = RetryConfig(base_delay=1.0, backoff_factor=10.0, max_delay=5.0, jitter=False)
        assert cfg.delay_for(10) == 5.0

    def test_jitter_increases_delay(self):
        cfg = RetryConfig(base_delay=1.0, backoff_factor=2.0, jitter=True)
        delays = [cfg.delay_for(0) for _ in range(20)]
        assert all(d >= 1.0 for d in delays)
        assert any(d > 1.0 for d in delays)  # at least one jittered value

    def test_is_retryable_returns_true_for_generic_exception(self):
        cfg = RetryConfig()
        assert cfg.is_retryable(RuntimeError("boom")) is True

    def test_is_retryable_returns_false_for_value_error(self):
        cfg = RetryConfig()
        assert cfg.is_retryable(ValueError("bad arg")) is False

    def test_is_retryable_respects_retryable_exceptions_filter(self):
        cfg = RetryConfig(retryable_exceptions=(ConnectionError,))
        assert cfg.is_retryable(ConnectionError()) is True
        assert cfg.is_retryable(RuntimeError("other")) is False

    def test_permanent_errors_never_retried(self):
        cfg = RetryConfig(retryable_exceptions=(ValueError,))
        # Even if listed explicitly, PERMANENT_ERRORS trump the filter
        for exc_cls in PERMANENT_ERRORS:
            try:
                exc = exc_cls("test")
            except TypeError:
                continue
            assert cfg.is_retryable(exc) is False


# ── with_retry ────────────────────────────────────────────────────────────────

class TestWithRetry:
    @patch("core.execution.retry.time.sleep")
    def test_success_on_first_attempt(self, mock_sleep):
        fn = MagicMock(return_value="ok")
        result = with_retry(fn, config=RetryConfig(max_attempts=3))
        assert result == "ok"
        fn.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("core.execution.retry.time.sleep")
    def test_retries_on_transient_error_and_succeeds(self, mock_sleep):
        fn = MagicMock(side_effect=[RuntimeError("timeout"), RuntimeError("timeout"), "ok"])
        result = with_retry(fn, config=RetryConfig(max_attempts=3, base_delay=0, jitter=False))
        assert result == "ok"
        assert fn.call_count == 3

    @patch("core.execution.retry.time.sleep")
    def test_raises_retry_exhausted_after_all_attempts(self, mock_sleep):
        fn = MagicMock(side_effect=RuntimeError("always fails"))
        with pytest.raises(RetryExhausted) as exc_info:
            with_retry(fn, config=RetryConfig(max_attempts=3, base_delay=0, jitter=False))
        assert fn.call_count == 3
        assert exc_info.value.attempts == 3
        assert isinstance(exc_info.value.last_error, RuntimeError)

    @patch("core.execution.retry.time.sleep")
    def test_no_retry_on_value_error(self, mock_sleep):
        fn = MagicMock(side_effect=ValueError("invalid symbol"))
        with pytest.raises(ValueError, match="invalid symbol"):
            with_retry(fn, config=RetryConfig(max_attempts=3))
        fn.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("core.execution.retry.time.sleep")
    def test_no_retry_on_type_error(self, mock_sleep):
        fn = MagicMock(side_effect=TypeError("bad type"))
        with pytest.raises(TypeError):
            with_retry(fn, config=RetryConfig(max_attempts=3))
        fn.assert_called_once()

    @patch("core.execution.retry.time.sleep")
    def test_max_attempts_1_means_no_retry(self, mock_sleep):
        fn = MagicMock(side_effect=RuntimeError("fail"))
        with pytest.raises(RetryExhausted) as exc_info:
            with_retry(fn, config=RetryConfig(max_attempts=1))
        fn.assert_called_once()
        mock_sleep.assert_not_called()
        assert exc_info.value.attempts == 1

    @patch("core.execution.retry.time.sleep")
    def test_sleep_called_between_retries(self, mock_sleep):
        fn = MagicMock(side_effect=[RuntimeError(), RuntimeError(), "ok"])
        cfg = RetryConfig(max_attempts=3, base_delay=2.0, backoff_factor=2.0, jitter=False)
        with_retry(fn, config=cfg)
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0] == call(2.0)   # attempt 0: 2.0s
        assert mock_sleep.call_args_list[1] == call(4.0)   # attempt 1: 4.0s

    @patch("core.execution.retry.time.sleep")
    def test_retryable_kwarg_overrides_config(self, mock_sleep):
        """Non-listed exception should NOT be retried when retryable is specified."""
        fn = MagicMock(side_effect=OSError("network"))
        with pytest.raises(RetryExhausted):
            with_retry(fn, retryable=(OSError,), config=RetryConfig(max_attempts=2, base_delay=0, jitter=False))
        assert fn.call_count == 2

    @patch("core.execution.retry.time.sleep")
    def test_retryable_kwarg_blocks_unlisted_exception(self, mock_sleep):
        fn = MagicMock(side_effect=RuntimeError("unrelated"))
        with pytest.raises(RuntimeError):
            with_retry(fn, retryable=(OSError,), config=RetryConfig(max_attempts=3))
        fn.assert_called_once()

    @patch("core.execution.retry.time.sleep")
    def test_retry_exhausted_message_contains_attempt_count(self, mock_sleep):
        fn = MagicMock(side_effect=ConnectionError("refused"))
        with pytest.raises(RetryExhausted) as exc_info:
            with_retry(fn, config=RetryConfig(max_attempts=2, base_delay=0, jitter=False))
        assert "2" in str(exc_info.value)

    @patch("core.execution.retry.time.sleep")
    def test_default_config_used_when_none_provided(self, mock_sleep):
        fn = MagicMock(return_value=42)
        result = with_retry(fn)
        assert result == 42
