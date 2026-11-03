import io
from types import SimpleNamespace

import pytest
from PIL import Image

from app.core.errors import ApiError
from app.services.attachments import AttachmentStore, validate, scan, MAX_TEXT
from app.services.store.memory import MemoryStore


def settings(tmp_path):
    return SimpleNamespace(attachment_storage_path=tmp_path, attachment_scanner_host="127.0.0.1", attachment_scanner_port=3310)


def test_image_signature_and_metadata_normalization():
    out = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(out, format="JPEG")
    mime, data, text = validate("screenshot.jpg", "image/jpeg", out.getvalue())
    assert mime == "image/png" and data.startswith(b"\x89PNG") and text is None
    with pytest.raises(ApiError):
        validate("screenshot.png", "image/png", out.getvalue())
    with pytest.raises(ApiError):
        validate("screenshot.jpg", "text/plain", out.getvalue())


@pytest.mark.parametrize("name,mime,data", [
    ("../report.txt", "text/plain", b"hello"),
    ("payload.exe", "application/octet-stream", b"MZpayload"),
    ("renamed.txt", "text/plain", b"MZpayload"),
    ("archive.txt", "text/plain", b"PK\x03\x04binary"),
    ("script.txt", "text/plain", b"#!/bin/sh\necho hi"),
    ("bad.json", "application/json", b"not JSON"),
    ("binary.txt", "text/plain", b"a\0b"),
    ("large.log", "text/plain", b"x" * (MAX_TEXT + 1)),
    ("script.js", "text/plain", b"alert(1)"),
])
def test_reject_unsupported_or_disguised_files(name, mime, data):
    with pytest.raises(ApiError):
        validate(name, mime, data)


def test_upload_owner_integrity_expiry_and_count(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.attachments.scan", lambda *_: None)
    store = MemoryStore()
    uploads = AttachmentStore(settings(tmp_path), store)
    item = uploads.upload("1", "error.log", "text/plain", b"Traceback: missing value")
    assert uploads.read("1", [item["id"]])[0]["data"].startswith(b"Traceback")
    assert (tmp_path / item["id"]).name != "error.log"
    with pytest.raises(ApiError) as error:
        uploads.read("2", [item["id"]])
    assert error.value.status_code == 404
    with pytest.raises(ApiError):
        uploads.read("1", [item["id"]] * 5)
    (tmp_path / item["id"]).write_bytes(b"changed")
    with pytest.raises(ApiError):
        uploads.read("1", [item["id"]])
    store.run_transaction(lambda txn: txn.update(f"uploads/{item['id']}", {"expiresAt": 0}))
    with pytest.raises(ApiError):
        uploads.read("1", [item["id"]])


def test_scanner_failure_rejects_without_storing(tmp_path, monkeypatch):
    def unavailable(*_):
        raise ApiError(503, "scanner_unavailable", "unavailable")
    monkeypatch.setattr("app.services.attachments.scan", unavailable)
    with pytest.raises(ApiError):
        AttachmentStore(settings(tmp_path), MemoryStore()).upload("1", "note.txt", "text/plain", b"hello")
    assert not list(tmp_path.iterdir())


def test_live_clamav_clean_and_test_signature(tmp_path):
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 3310), timeout=1):
            pass
    except OSError:
        pytest.skip("Local ClamAV unavailable")
    scan(b"A harmless repair report", settings(tmp_path))
    # Standard antivirus test signature, assembled here; no executable is created.
    sample = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    with pytest.raises(ApiError) as error:
        scan(sample, settings(tmp_path))
    assert error.value.code == "unsafe_attachment"
