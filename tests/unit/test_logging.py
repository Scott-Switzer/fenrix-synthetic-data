"""Unit tests for structured logging with secret redaction."""

import io
import json
import logging
import sys

from fenrix_synthetic.storage.logging import (
    RedactingFilter,
    get_logger,
    setup_logging,
)


class TestRedactingFilter:
    """Test RedactingFilter."""

    def test_redact_password_in_message(self):
        filter = RedactingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="password=secret123",
            args=(),
            exc_info=None,
        )
        filter.filter(record)
        assert "secret123" not in record.msg
        assert "***" in record.msg

    def test_redact_token_in_message(self):
        filter = RedactingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg='token="abc123"',
            args=(),
            exc_info=None,
        )
        filter.filter(record)
        assert "abc123" not in record.msg

    def test_redact_secret_key_in_message(self):
        filter = RedactingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="secret: mysecret",
            args=(),
            exc_info=None,
        )
        filter.filter(record)
        assert "mysecret" not in record.msg

    def test_redact_api_key_in_message(self):
        filter = RedactingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="api_key=xyz789",
            args=(),
            exc_info=None,
        )
        filter.filter(record)
        assert "xyz789" not in record.msg

    def test_redact_auth_in_message(self):
        filter = RedactingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="auth=bearer123",
            args=(),
            exc_info=None,
        )
        filter.filter(record)
        assert "bearer123" not in record.msg

    def test_redact_credential_in_message(self):
        filter = RedactingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="credential=secret",
            args=(),
            exc_info=None,
        )
        filter.filter(record)
        assert "secret" not in record.msg

    def test_redact_case_insensitive(self):
        filter = RedactingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="PASSWORD=secret",
            args=(),
            exc_info=None,
        )
        filter.filter(record)
        assert "PASSWORD" in record.msg
        assert "secret" not in record.msg

    def test_redact_in_args(self):
        filter = RedactingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="User %s logged in",
            args=("password=secret",),
            exc_info=None,
        )
        filter.filter(record)
        # CPython stores single-element tuple as its content
        if isinstance(record.args, tuple):
            assert "***" in str(record.args)
        else:
            assert "***" in str(record.args)

    def test_redact_in_dict_args(self):
        filter = RedactingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Config: %s",
            args=({"api_key": "secret123"},),
            exc_info=None,
        )
        filter.filter(record)
        # CPython unwraps single-element tuple with dict
        if isinstance(record.args, dict):
            assert record.args.get("api_key") == "***"

    def test_non_secret_values_unchanged(self):
        filter = RedactingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="user=john action=login",
            args=(),
            exc_info=None,
        )
        filter.filter(record)
        assert "user=john" in record.msg

    def test_extra_fields_redacted(self):
        filter = RedactingFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        record.__dict__["extra_field"] = "password=secret"
        filter.filter(record)
        assert "secret" not in record.__dict__["extra_field"]


class TestSetupLogging:
    """Test setup_logging function."""

    def test_setup_json_logging(self):
        # Capture stderr output
        stderr = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = stderr
        try:
            setup_logging(level="DEBUG", format_type="json")
            logger = logging.getLogger("test_json")
            logger.info("test message")
            output = stderr.getvalue()
            assert "test message" in output
            # Should be valid JSON
            log_entry = json.loads(output.strip().split("\n")[-1])
            assert log_entry["level"] == "INFO"
        finally:
            sys.stderr = old_stderr

    def test_setup_text_logging(self):
        stderr = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = stderr
        try:
            setup_logging(level="DEBUG", format_type="text")
            logger = logging.getLogger("test_text")
            logger.debug("debug message")
            output = stderr.getvalue()
            assert "debug message" in output
        finally:
            sys.stderr = old_stderr

    def test_redaction_in_configured_logger(self):
        stderr = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = stderr
        try:
            setup_logging(level="INFO", format_type="json")
            logger = logging.getLogger("test_redact")
            logger.info("password=secret123")
            output = stderr.getvalue()
            assert "***" in output
        finally:
            sys.stderr = old_stderr


class TestGetLogger:
    """Test get_logger function."""

    def test_get_logger_returns_logger(self):
        logger = get_logger("test.module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test.module"
