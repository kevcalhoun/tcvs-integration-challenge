"""Structured logging configuration."""

import logging
import sys
from typing import Any

import structlog

from src.config import get_settings


def configure_logging() -> None:
    """Configure structured logging for the application."""
    settings = get_settings()

    # Set up standard logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper()),
    )

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer() if settings.environment == "development"
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    """Get a logger instance for the given module name.

    Returns a structlog bound logger that supports standard logging methods
    (info, warning, error, etc.) with structured context.
    """
    configure_logging()
    return structlog.get_logger(name)
