"""Bounded, owner-scoped context uploads. Never served as HTML or executed."""
import hashlib
import io
import json
import os
import re
import secrets
import socket
import struct
import time
import warnings
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.core.errors import ApiError

MAX_FILE = 5 * 1024 * 1024
MAX_TEXT = 128 * 1024
MAX_COUNT = 4
MAX_TOTAL = 10 * 1024 * 1024
TTL = 24 * 3600
IMAGE_TYPES = {".png": ("image/png", "PNG"), ".jpg": ("image/jpeg", "JPEG"),
               ".jpeg": ("image/jpeg", "JPEG"), ".webp": ("image/webp", "WEBP")}
TEXT_TYPES = {".txt": {"text/plain"}, ".log": {"text/plain"}, ".md": {"text/plain", "text/markdown"},
              ".json": {"application/json", "text/plain"}, ".yaml": {"application/yaml", "application/x-yaml", "text/yaml", "text/plain"}}


def scan(data: bytes, settings) -> None:
    """ClamD INSTREAM: no host paths or shell commands, and no skip-scan fallback."""
    try:
        with socket.create_connection((settings.attachment_scanner_host, settings.attachment_scanner_port), timeout=5) as conn:
            conn.settimeout(30)
            conn.sendall(b"zINSTREAM\0")
            for start in range(0, len(data), 65536):
                chunk = data[start:start + 65536]
                conn.sendall(struct.pack("!I", len(chunk)) + chunk)
            conn.sendall(b"\0\0\0\0")
            response = b""
            while b"\0" not in response and len(response) < 4096:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                response += chunk
    except OSError:
        raise ApiError(503, "scanner_unavailable", "File checks are temporarily unavailable. Try again shortly.") from None
    if b"FOUND" in response:
        raise ApiError(422, "unsafe_attachment", "This file did not pass the safety check. Choose another file.")
    if response.rstrip(b"\0\n") != b"stream: OK":
        raise ApiError(503, "scanner_unavailable", "The file check could not finish. Try again shortly.")


def validate(name: str, mime: str, data: bytes) -> tuple[str, bytes, str | None]:
    if not name or len(name) > 120 or Path(name).name != name or re.search(r"[\\/:\x00-\x1f]", name):
        raise ApiError(422, "invalid_attachment", "Choose a file with a simple filename.")
    ext = Path(name).suffix.lower()
    if not data or len(data) > MAX_FILE:
        raise ApiError(422, "attachment_size", "Each image must be under 5 MB and each text file under 128 KB.")
    if data.startswith((b"MZ", b"\x7fELF", b"PK\x03\x04", b"\x1f\x8b", b"Rar!", b"7z\xbc\xaf\x27\x1c", b"\xd0\xcf\x11\xe0", b"\x00asm")):
        raise ApiError(422, "unsupported_attachment", "Executables, archives and unsupported binaries cannot be attached.")
    mime = mime.split(";", 1)[0].lower()
    if ext in IMAGE_TYPES:
        expected, fmt = IMAGE_TYPES[ext]
        if mime != expected:
            raise ApiError(422, "invalid_attachment", "The file type does not match its name.")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    if image.format != fmt or image.width * image.height > 8_000_000 or getattr(image, "n_frames", 1) != 1:
                        raise ValueError()
                    image.verify()
                with Image.open(io.BytesIO(data)) as image:
                    image.thumbnail((1600, 1600))
                    out = io.BytesIO()
                    image.convert("RGB").save(out, format="PNG")
            return "image/png", out.getvalue(), None
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ApiError(422, "invalid_attachment", "This image could not be read. Choose a valid, non-animated image under 8 megapixels.") from None
    if ext not in TEXT_TYPES or mime not in TEXT_TYPES[ext] | {"application/octet-stream", ""}:
        raise ApiError(422, "unsupported_attachment", "Choose a PNG, JPG, WEBP, TXT, LOG, MD, JSON or YAML file.")
    if len(data) > MAX_TEXT:
        raise ApiError(422, "attachment_size", "Text files must be under 128 KB.")
    try:
        text = data.decode("utf-8-sig")
        if any(ord(c) < 32 and c not in "\r\n\t" for c in text) or text.lstrip().startswith(("#!", "<?php", "<script")):
            raise ValueError()
        if ext == ".json":
            json.loads(text)
    except (UnicodeError, ValueError, RecursionError):
        raise ApiError(422, "invalid_attachment", "Choose a valid UTF-8 text file matching its extension.") from None
    return "text/plain", data, text


class AttachmentStore:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.root = Path(settings.attachment_storage_path).resolve()

    def cleanup(self) -> None:
        if not self.root.is_dir():
            return
        cutoff = time.time() - TTL
        for path in self.root.iterdir():
            if re.fullmatch(r"att_[0-9a-f]{32}", path.name) and not path.is_symlink():
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink(missing_ok=True)
                except OSError:
                    continue

    def read(self, user: str, ids: list[str]) -> list[dict]:
        if len(ids) > MAX_COUNT or len(set(ids)) != len(ids):
            raise ApiError(422, "attachment_count", "Attach up to four different files.")
        rows = []
        for id_ in ids:
            if not re.fullmatch(r"att_[0-9a-f]{32}", id_):
                raise ApiError(422, "invalid_attachment", "Invalid attachment.")
            row = self.store.get(f"uploads/{id_}")
            if not row or row["userId"] != user:
                raise ApiError(404, "not_found", "Attachment not found.")
            if row["expiresAt"] <= time.time():
                raise ApiError(410, "attachment_expired", "An attachment expired. Please attach it again.")
            path = self.root / id_
            if path.is_symlink() or not path.is_file():
                raise ApiError(410, "attachment_expired", "An attachment is no longer available. Please attach it again.")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ApiError(422, "invalid_attachment", "Attachment integrity check failed.")
            rows.append({**row, "data": data})
        if sum(len(row["data"]) for row in rows) > MAX_TOTAL:
            raise ApiError(422, "attachment_size", "Attachments must total under 10 MB.")
        return rows

    def upload(self, user: str, name: str, mime: str, data: bytes) -> dict:
        # Validate cheap bounds before contacting the scanner or decoding images.
        if not data or len(data) > MAX_FILE:
            raise ApiError(413, "attachment_size", "Files must be under 5 MB.")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        pending = 0
        now = time.time()
        for path in self.root.iterdir():
            if not re.fullmatch(r"att_[0-9a-f]{32}", path.name) or path.is_symlink():
                continue
            if path.stat().st_mtime < now - TTL:
                path.unlink(missing_ok=True)
            else:
                row = self.store.get(f"uploads/{path.name}")
                pending += bool(row and row["userId"] == user)
        if pending >= 20:
            raise ApiError(429, "attachment_quota", "Your temporary upload limit is reached. Try again later.")
        # Reject unsupported names/types before scan; parse only scanned bytes.
        ext = Path(name).suffix.lower()
        if ext not in IMAGE_TYPES and ext not in TEXT_TYPES:
            raise ApiError(422, "unsupported_attachment", "This file type is not supported.")
        validate(name, mime, data) if ext in TEXT_TYPES else None
        scan(data, self.settings)
        normalized_mime, normalized, text = validate(name, mime, data)
        id_ = "att_" + secrets.token_hex(16)
        path = self.root / id_
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(normalized)
        row = {"id": id_, "userId": user, "filename": name, "size": len(data),
               "mime": normalized_mime, "sha256": hashlib.sha256(normalized).hexdigest(), "expiresAt": now + TTL}
        try:
            self.store.set(f"uploads/{id_}", row)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return {key: row[key] for key in ("id", "filename", "size", "mime", "expiresAt")}
