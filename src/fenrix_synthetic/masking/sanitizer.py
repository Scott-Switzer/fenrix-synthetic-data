from __future__ import annotations

import hashlib
import re
from typing import Any


def sanitize_path_name(source: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_/-]", "_", source)
    return clean


def sanitize_metadata_value(value: str, registry_values: set[str]) -> str:
    for private in sorted(registry_values, key=len, reverse=True):
        escaped = re.escape(private)
        value = re.sub(escaped, "[REDACTED]", value, flags=re.IGNORECASE)
    return value


def sanitize_metadata(
    metadata: dict[str, Any],
    registry_values: set[str],
    skip_keys: set[str] | None = None,
) -> dict[str, Any]:
    skip = skip_keys or set()
    result: dict[str, Any] = {}
    for key, val in metadata.items():
        if key in skip:
            result[key] = val
            continue
        if isinstance(val, str):
            result[key] = sanitize_metadata_value(val, registry_values)
        elif isinstance(val, list):
            result[key] = [
                sanitize_metadata_value(v, registry_values) if isinstance(v, str) else v
                for v in val
            ]
        else:
            result[key] = val
    return result


def compute_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
