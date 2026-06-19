"""Structured JSON logging with secret redaction."""

import logging
import re
import sys
from collections.abc import Mapping
from typing import Any

from pythonjsonlogger import jsonlogger


class RedactingFilter(logging.Filter):
    """Filter that redacts secret values from log records."""

    def __init__(self, pattern: str = r"(?i)(key|token|secret|password|auth|credential)"):
        super().__init__()
        self.pattern = re.compile(pattern)

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact sensitive values in log record."""
        if isinstance(record.msg, str):
            record.msg = self._redact_string(record.msg)

        if record.args:
            record.args = self._redact_args(record.args)

        for key, value in record.__dict__.items():
            if key in (
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "exc_info",
                "exc_text",
                "stack_info",
                "getMessage",
            ):
                continue
            if isinstance(value, str):
                record.__dict__[key] = self._redact_string(value)
            elif isinstance(value, dict):
                record.__dict__[key] = self._redact_dict(value)
            elif isinstance(value, (list, tuple)):
                record.__dict__[key] = self._redact_sequence(value)

        return True

    def _redact_string(self, s: str) -> str:
        """Redact values that look like secrets."""

        def replace_match(match: re.Match[str]) -> str:
            key = match.group(1)
            sep = match.group(2)
            if self.pattern.search(key):
                return f'{key}{sep}"***"'
            return match.group(0)

        s = re.sub(r'(\w+)\s*([=:])\s*["\']?([^"\'\s]+)["\']?', replace_match, s)
        s = re.sub(r'["\'](\w+)["\']\s*:\s*["\']([^"\']+)["\']', replace_match, s)
        return s

    def _redact_dict(self, d: Mapping[str, Any]) -> dict[str, Any]:
        """Redact sensitive values in dict."""
        result: dict[str, Any] = {}
        for k, v in d.items():
            if self.pattern.search(str(k)):
                result[k] = "***"
            elif isinstance(v, str):
                result[k] = self._redact_string(v)
            elif isinstance(v, dict):
                result[k] = self._redact_dict(v)
            elif isinstance(v, (list, tuple)):
                result[k] = self._redact_sequence(v)
            else:
                result[k] = v
        return result

    def _redact_sequence(self, seq: list | tuple) -> list | tuple:
        """Redact sensitive values in sequence."""
        result: list[Any] = []
        for item in seq:
            if isinstance(item, str):
                result.append(self._redact_string(item))
            elif isinstance(item, dict):
                result.append(self._redact_dict(item))
            elif isinstance(item, (list, tuple)):
                result.append(self._redact_sequence(item))
            else:
                result.append(item)
        return type(seq)(result)

    def _redact_args(
        self, args: tuple[object, ...] | Mapping[str, object]
    ) -> tuple[object, ...] | dict[str, Any]:
        """Redact sensitive values in log args."""
        if isinstance(args, Mapping):
            return self._redact_dict(dict(args))
        result: list[object] = []
        for arg in args:
            if isinstance(arg, str):
                result.append(self._redact_string(arg))
            elif isinstance(arg, dict):
                result.append(self._redact_dict(arg))
            elif isinstance(arg, (list, tuple)):
                result.append(self._redact_sequence(arg))
            else:
                result.append(arg)
        return tuple(result)


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter with extra fields."""

    def add_fields(self, log_record: dict, record: logging.LogRecord, message_dict: dict) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["timestamp"] = record.created
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)


def setup_logging(
    level: str = "INFO",
    format_type: str = "json",
    secret_pattern: str = r"(?i)(key|token|secret|password|auth|credential)",
) -> None:
    """Configure application logging."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)

    formatter: logging.Formatter
    if format_type.lower() == "json":
        formatter = CustomJsonFormatter(
            "%(timestamp)s %(level)s %(name)s %(message)s",
            timestamp=True,
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler.setFormatter(formatter)
    handler.addFilter(RedactingFilter(secret_pattern))
    root_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    return logging.getLogger(name)
