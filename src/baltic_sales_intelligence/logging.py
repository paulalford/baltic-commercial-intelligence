"""Reusable JSON logging for pipeline stages."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import IO, Any

_STANDARD_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__)
_EVENT_FIELDS = ("stage", "dataset", "batch_id", "action", "count", "error_category")


class JsonFormatter(logging.Formatter):
    """Format one structured JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _EVENT_FIELDS:
            payload[field] = getattr(record, field, None)
        for key, value in record.__dict__.items():
            is_custom = key not in _STANDARD_RECORD_FIELDS and key not in payload
            if is_custom and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def configure_logging(
    level: str = "INFO", *, stream: IO[str] | None = None
) -> logging.Logger:
    """Configure and return the project logger without duplicating handlers."""

    numeric_level = logging.getLevelNamesMapping().get(level.upper())
    if not isinstance(numeric_level, int):
        raise ValueError(f"unsupported logging level: {level}")

    logger = logging.getLogger("baltic_sales_intelligence")
    logger.handlers.clear()
    logger.setLevel(numeric_level)
    logger.propagate = False

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    stage: str,
    action: str,
    dataset: str | None = None,
    batch_id: str | None = None,
    count: int | None = None,
    error_category: str | None = None,
) -> None:
    """Log the required pipeline event fields without accepting row payloads."""

    logger.log(
        level,
        message,
        extra={
            "stage": stage,
            "dataset": dataset,
            "batch_id": batch_id,
            "action": action,
            "count": count,
            "error_category": error_category,
        },
    )
