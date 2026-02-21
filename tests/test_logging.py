"""
Tests for ``core.logging`` – structured logging setup.

Verifies:
  - JSON and text renderers are wired correctly
  - Console and file handlers are attached (or not) per config
  - Log-level gating works
  - Idempotent guard (`_CONFIGURED` flag)
  - stdlib loggers are routed through structlog pipeline
  - ``reset_logging()`` clears state for test isolation
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import structlog

from core.config import LoggingConfig
from core.logging import reset_logging, setup_logging


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_logging():
    """Reset logging state before and after every test."""
    reset_logging()
    yield
    reset_logging()


def _make_config(**overrides) -> LoggingConfig:
    """Build a LoggingConfig with sensible test defaults."""
    defaults = {
        "level": "DEBUG",
        "format": "json",
        "include_timestamp": True,
        "include_context": True,
        "console": True,
        "file": False,
        "file_path": "logs/test.log",
        "max_file_size_mb": 1,
        "backup_count": 0,
    }
    defaults.update(overrides)
    return LoggingConfig(**defaults)


# ── Basic setup ──────────────────────────────────────────────────────────────


class TestSetupLogging:
    """Core setup_logging behaviour."""

    def test_configures_root_logger_level(self):
        setup_logging(_make_config(level="WARNING"))
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_console_handler_attached_when_enabled(self):
        setup_logging(_make_config(console=True, file=False))
        root = logging.getLogger()
        stream_handlers = [
            h for h in root.handlers if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert len(stream_handlers) == 1

    def test_no_console_handler_when_disabled(self):
        setup_logging(_make_config(console=False, file=False))
        root = logging.getLogger()
        stream_handlers = [
            h for h in root.handlers if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert len(stream_handlers) == 0

    def test_file_handler_attached_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "test.log")
            setup_logging(_make_config(file=True, file_path=log_path))
            root = logging.getLogger()
            from logging.handlers import RotatingFileHandler
            file_handlers = [
                h for h in root.handlers
                if isinstance(h, RotatingFileHandler)
            ]
            assert len(file_handlers) == 1

    def test_no_file_handler_when_disabled(self):
        setup_logging(_make_config(file=False))
        root = logging.getLogger()
        from logging.handlers import RotatingFileHandler
        file_handlers = [
            h for h in root.handlers
            if isinstance(h, RotatingFileHandler)
        ]
        assert len(file_handlers) == 0

    def test_log_directory_created_automatically(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = os.path.join(tmp, "sub", "dir", "app.log")
            setup_logging(_make_config(file=True, file_path=nested))
            assert os.path.isdir(os.path.join(tmp, "sub", "dir"))


# ── Idempotency ──────────────────────────────────────────────────────────────


class TestIdempotency:
    """setup_logging should only run once per process."""

    def test_second_call_is_noop(self):
        setup_logging(_make_config(level="DEBUG"))
        root = logging.getLogger()
        handler_count = len(root.handlers)

        # Call again — handlers should NOT double
        setup_logging(_make_config(level="ERROR"))
        assert len(root.handlers) == handler_count
        # Level should remain DEBUG (first call wins)
        assert root.level == logging.DEBUG

    def test_reset_allows_reconfiguration(self):
        setup_logging(_make_config(level="DEBUG"))
        assert logging.getLogger().level == logging.DEBUG

        reset_logging()

        setup_logging(_make_config(level="ERROR"))
        assert logging.getLogger().level == logging.ERROR


# ── JSON output ──────────────────────────────────────────────────────────────


class TestJSONRenderer:
    """JSON format produces valid JSON with expected fields."""

    def test_json_output_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "json.log")
            setup_logging(_make_config(
                format="json",
                console=False,
                file=True,
                file_path=log_path,
            ))

            log = structlog.get_logger("test.json")
            log.info("hello", key="value")

            # Force flush
            for h in logging.getLogger().handlers:
                h.flush()

            content = Path(log_path).read_text().strip()
            assert content, "Log file should not be empty"
            record = json.loads(content)
            assert record["msg"] == "hello"
            assert record["key"] == "value"
            assert record["level"] == "info"

    def test_json_output_includes_timestamp_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "ts.log")
            setup_logging(_make_config(
                format="json",
                include_timestamp=True,
                console=False,
                file=True,
                file_path=log_path,
            ))

            log = structlog.get_logger("test.ts")
            log.info("with_ts")

            for h in logging.getLogger().handlers:
                h.flush()

            record = json.loads(Path(log_path).read_text().strip())
            assert "timestamp" in record


class TestTextRenderer:
    """Text format produces human-readable (non-JSON) output."""

    def test_text_output_is_not_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "text.log")
            setup_logging(_make_config(
                format="text",
                console=False,
                file=True,
                file_path=log_path,
            ))

            log = structlog.get_logger("test.text")
            log.info("hello_text")

            for h in logging.getLogger().handlers:
                h.flush()

            content = Path(log_path).read_text().strip()
            assert content
            # ConsoleRenderer produces something like:
            #  [info     ] hello_text
            with pytest.raises(json.JSONDecodeError):
                json.loads(content)


# ── Level gating ─────────────────────────────────────────────────────────────


class TestLevelGating:
    """Messages below the configured level are suppressed."""

    def test_debug_suppressed_at_info_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "level.log")
            setup_logging(_make_config(
                level="INFO",
                format="json",
                console=False,
                file=True,
                file_path=log_path,
            ))

            log = structlog.get_logger("test.level")
            log.debug("should_not_appear")
            log.info("should_appear")

            for h in logging.getLogger().handlers:
                h.flush()

            lines = [
                l for l in Path(log_path).read_text().strip().splitlines()
                if l.strip()
            ]
            assert len(lines) == 1
            assert "should_appear" in lines[0]


# ── stdlib wrapping ──────────────────────────────────────────────────────────


class TestStdlibWrapping:
    """stdlib logging.getLogger() calls go through structlog pipeline."""

    def test_stdlib_logger_output_is_formatted(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "stdlib.log")
            setup_logging(_make_config(
                format="json",
                console=False,
                file=True,
                file_path=log_path,
            ))

            stdlib_logger = logging.getLogger("some.module")
            stdlib_logger.info("from_stdlib", extra={})

            for h in logging.getLogger().handlers:
                h.flush()

            content = Path(log_path).read_text().strip()
            assert content
            record = json.loads(content)
            # stdlib messages get the event key renamed to "msg"
            # (or stay as "event") depending on processor chain
            assert "from_stdlib" in content

    def test_existing_binance_logger_routed(self):
        """Simulates what core.data.binance_client does."""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "binance.log")
            setup_logging(_make_config(
                format="json",
                console=False,
                file=True,
                file_path=log_path,
            ))

            binance_log = logging.getLogger("core.data.binance_client")
            binance_log.info("Fetching candles for BTCUSDT")

            for h in logging.getLogger().handlers:
                h.flush()

            content = Path(log_path).read_text().strip()
            assert "BTCUSDT" in content


# ── structlog.get_logger usage ───────────────────────────────────────────────


class TestStructlogGetLogger:
    """structlog.get_logger() returns a usable bound logger."""

    def test_bound_logger_with_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "ctx.log")
            setup_logging(_make_config(
                format="json",
                console=False,
                file=True,
                file_path=log_path,
            ))

            log = structlog.get_logger("test.ctx")
            bound = log.bind(strategy="smart_hodler")
            bound.info("signal", signal="BUY", symbol="BTCUSDT")

            for h in logging.getLogger().handlers:
                h.flush()

            record = json.loads(Path(log_path).read_text().strip())
            assert record["strategy"] == "smart_hodler"
            assert record["signal"] == "BUY"
            assert record["symbol"] == "BTCUSDT"


# ── File rotation config ────────────────────────────────────────────────────


class TestFileRotation:
    """RotatingFileHandler is configured with correct max size."""

    def test_max_bytes_matches_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "rotate.log")
            setup_logging(_make_config(
                file=True,
                file_path=log_path,
                max_file_size_mb=5,
                backup_count=3,
            ))

            from logging.handlers import RotatingFileHandler
            root = logging.getLogger()
            rfh = next(
                h for h in root.handlers
                if isinstance(h, RotatingFileHandler)
            )
            assert rfh.maxBytes == 5 * 1024 * 1024
            assert rfh.backupCount == 3


# ── reset_logging ────────────────────────────────────────────────────────────


class TestResetLogging:
    """reset_logging clears handlers and allows reconfiguration."""

    def test_handlers_cleared_after_reset(self):
        setup_logging(_make_config(console=True, file=False))
        assert len(logging.getLogger().handlers) > 0

        reset_logging()
        assert len(logging.getLogger().handlers) == 0
