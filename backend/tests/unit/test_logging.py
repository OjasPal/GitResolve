import json
import logging

from app.core.config import Settings
from app.core.logging import JsonFormatter, configure_logging


def record(message: str, **extra) -> logging.LogRecord:
    r = logging.LogRecord("gitresolve.worker", logging.WARNING, __file__, 1, message, (), None)
    for key, value in extra.items():
        setattr(r, key, value)
    return r


def test_json_log_has_cloud_logging_fields_and_extras():
    line = json.loads(JsonFormatter().format(record("lease lost", task_id="tsk_1", attempt=2)))
    assert line["severity"] == "WARNING" and line["message"] == "lease lost"
    assert line["task_id"] == "tsk_1" and line["attempt"] == 2


def test_json_log_redacts_secrets_in_messages_and_extras():
    out = JsonFormatter().format(record("token ghp_" + "a" * 30, header="Authorization: Bearer abc.def.ghi"))
    assert "ghp_" not in out and "Bearer abc" not in out


def test_json_format_is_automatic_in_production_only():
    assert Settings(environment="production", _env_file=None).log_json is True
    assert Settings(environment="local", _env_file=None).log_json is False
    assert Settings(environment="local", log_format="json", _env_file=None).log_json is True
    configure_logging(Settings(environment="test", _env_file=None))  # must not raise
