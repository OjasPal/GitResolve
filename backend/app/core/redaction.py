"""Redaction applied before anything is stored in events or artifacts."""

import re

_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"(?i)\bauthorization\s*[:=]\s*(?:bearer|token|basic)?\s*\S+"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),
    re.compile(
        r"(?i)\b(?:[a-z][a-z0-9]*_)*(api[_-]?key|secret|token|password|passwd)\b['\"]?\s*[:=]\s*['\"]?[^\s'\"]+"
    ),
]
# Host paths only: repository-relative diagnostics remain intact.
_PATTERNS += [
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_.~+/=-]+"),
    re.compile(r"\bAQ\.[A-Za-z0-9_-]{20,}"),
]
_HOST_PATHS = [
    re.compile(r"(?i)(?<![a-z0-9])[a-z]:[\\/]+[^\r\n'\"<>|]*"),
    re.compile(r"(?<![\w.])/(?:home|Users|tmp|var/tmp|private/tmp|var/folders|private/var/folders)(?:/[^\s'\"<>]*)?"),
    re.compile(r"\\\\[^\s'\"]+"),
]
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def redact(text: str, max_chars: int | None = None, *, host_paths: bool = True) -> str:
    text = _ANSI.sub("", text)
    text = _CONTROL.sub("", text)
    for pattern in _PATTERNS + (_HOST_PATHS if host_paths else []):
        text = pattern.sub("[REDACTED]", text)
    if max_chars is not None and len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text


def redact_value(value):
    """Recursively sanitize public payloads, including dictionary keys."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            private = isinstance(key, str) and re.fullmatch(
                r"(?i)(?:api[_-]?key|secret|password|passwd|access[_-]?token|authorization)", key
            )
            result[redact(key) if isinstance(key, str) else key] = "[REDACTED]" if private else redact_value(item)
        return result
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    return value
