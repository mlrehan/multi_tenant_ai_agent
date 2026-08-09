"""Structured (JSON) logging setup, stdlib-only.

Every log record is enriched with the current ``RequestContext`` (correlation
id, request id) when one is bound, so logs can be joined with traces/audit
rows by ``correlation_id`` without threading it through every call site.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from iam_platform.core.context import current as current_context

_SENSITIVE_KEYS = {"password", "token", "refresh_token", "access_token", "secret", "authorization"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
        }

        ctx = current_context()
        if ctx is not None:
            payload["request_id"] = str(ctx.request_id)
            payload["correlation_id"] = str(ctx.correlation_id)

        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            for key, value in extra.items():
                if key.lower() in _SENSITIVE_KEYS:
                    continue  # never log passwords/tokens/secrets, per docs/03-threat-model.md
                payload[key] = value

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
