"""Structured logging tests."""

import io
import json
import logging

import pytest

from baltic_sales_intelligence.logging import configure_logging, log_event


def test_structured_log_contains_required_event_fields() -> None:
    stream = io.StringIO()
    logger = configure_logging("INFO", stream=stream)

    log_event(
        logger,
        logging.INFO,
        "configuration loaded",
        stage="foundation",
        dataset="configuration",
        batch_id="abc123",
        action="validate",
        count=4,
    )
    payload = json.loads(stream.getvalue())

    assert payload["stage"] == "foundation"
    assert payload["dataset"] == "configuration"
    assert payload["batch_id"] == "abc123"
    assert payload["action"] == "validate"
    assert payload["count"] == 4
    assert payload["error_category"] is None
    assert payload["message"] == "configuration loaded"


def test_reconfiguration_does_not_duplicate_handlers() -> None:
    configure_logging("INFO", stream=io.StringIO())
    logger = configure_logging("DEBUG", stream=io.StringIO())

    assert logger.level == logging.DEBUG
    assert len(logger.handlers) == 1


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported logging level"):
        configure_logging("VERBOSE")
