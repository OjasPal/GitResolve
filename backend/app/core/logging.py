"""Structured JSON logs for Cloud Logging (`severity` and `message` are recognised fields)."""

import json
import logging
import sys

from app.core.config import Settings
from app.core.redaction import redact

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "severity": record.levelname,
            "message": redact(record.getMessage(), 2000),
            "logger": record.name,
        }
        for key, value in record.__dict__.items():  # fields passed via `extra=`
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value if isinstance(value, (int, float, bool, type(None))) else redact(str(value), 500)
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info), 4000)
        return json.dumps(payload, default=str)


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
