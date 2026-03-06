"""
Structured logging setup using *structlog*.

Provides a single ``setup_logging()`` entry-point that:

1. Configures structlog's processor pipeline (timestamps, log level,
   JSON or human-readable console output).
2. Wraps the Python standard-library ``logging`` module so that
   existing ``logging.getLogger()`` calls (e.g. in ``binance_client``
   and ``loader``) are routed through the same pipeline.
3. Adds a ``StreamHandler`` (when ``console=True``) and a
   ``RotatingFileHandler`` (when ``file=True``).

Call ``setup_logging()`` once at application startup.  The function is
**idempotent** — subsequent calls are silently ignored.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from core.config import LoggingConfig

_CONFIGURED = False


def setup_logging(config: "LoggingConfig") -> None:
    """
    Configure structlog + stdlib logging from a ``LoggingConfig``.

    Args:
        config: Pydantic model with level, format, console, file, etc.
    """
    global _CONFIGURED  # noqa: PLW0603
    if _CONFIGURED:
        return
    _CONFIGURED = True

    log_level = getattr(logging, config.level.upper(), logging.INFO)

    # ── Shared processors (used by both structlog and stdlib) ────────
    shared_processors: list[structlog.types.Processor] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
    ]

    if config.include_timestamp:
        shared_processors.append(
            structlog.processors.TimeStamper(fmt="iso", utc=True)
        )

    if config.include_context:
        shared_processors.append(
            structlog.processors.EventRenamer("msg"),
        )

    shared_processors.append(structlog.processors.StackInfoRenderer())
    shared_processors.append(structlog.processors.UnicodeDecoder())

    # ── Renderer ─────────────────────────────────────────────────────
    if config.format == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    # ── structlog configuration ──────────────────────────────────────
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # ── stdlib formatter (renders via structlog pipeline) ────────────
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    # ── Handlers ─────────────────────────────────────────────────────
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove pre-existing handlers to avoid duplicate output, but
    # preserve Celery's own handlers (they live on the 'celery*'
    # loggers) so that task success/failure messages still appear.
    root_logger.handlers = [
        h for h in root_logger.handlers
        if getattr(h, "_celery", False)
    ]

    if config.console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(log_level)
        root_logger.addHandler(console_handler)

    if config.file:
        log_dir = os.path.dirname(config.file_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        file_handler = RotatingFileHandler(
            filename=config.file_path,
            maxBytes=config.max_file_size_mb * 1024 * 1024,
            backupCount=config.backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(log_level)
        root_logger.addHandler(file_handler)


def reset_logging() -> None:
    """
    Reset the module-level ``_CONFIGURED`` flag.

    Used only in tests to allow ``setup_logging()`` to be called again
    with a different configuration.
    """
    global _CONFIGURED  # noqa: PLW0603
    _CONFIGURED = False

    root = logging.getLogger()
    root.handlers.clear()

    structlog.reset_defaults()
