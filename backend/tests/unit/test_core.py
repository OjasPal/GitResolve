import pytest

from app.core.config import Settings
from app.core.redaction import redact
from app.core.security import create_session_token, verify_session_token
from app.core.errors import ApiError
from app.services.ids import sha256_hash, task_id_for
from app.services.store.base import AlreadyExists
from app.services.store.memory import MemoryStore


def test_redact_removes_secrets_and_control_characters():
    text = "token ghp_" + "a" * 30 + "\x1b[31m red\x1b[0m\nAuthorization: Bearer abc.def.ghi\napi_key=supersecret123"
    out = redact(text)
    assert "ghp_" not in out and "supersecret123" not in out and "Bearer abc" not in out
    assert "\x1b" not in out


def test_redact_removes_private_key_blocks_and_truncates():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
    assert "MIIabc" not in redact(pem)
    assert len(redact("x" * 1000, 500)) == 500


def test_task_id_is_deterministic_per_user_and_key():
    a = task_id_for("1", "key")
    assert a == task_id_for("1", "key")
    assert a != task_id_for("2", "key")
    assert a != task_id_for("1", "other")
    assert len(a) == 4 + 20
    assert task_id_for("1", None) != task_id_for("1", None)
    assert sha256_hash("x").startswith("sha256:") and len(sha256_hash("x")) == 7 + 64


def test_jwt_round_trip_and_rejection():
    s = Settings(environment="test", _env_file=None)
    token, _ = create_session_token(s, "42")
    assert verify_session_token(s, token) == "42"
    with pytest.raises(ApiError):
        verify_session_token(s, token + "x")
    other = Settings(environment="test", session_jwt_secret="another-secret-another-secret-1234", _env_file=None)
    with pytest.raises(ApiError):
        verify_session_token(other, token)
    expired = Settings(environment="test", jwt_ttl_minutes=-1, _env_file=None)
    old, _ = create_session_token(expired, "42")
    with pytest.raises(ApiError):
        verify_session_token(s, old)


def test_production_guard_rejects_weak_configuration():
    with pytest.raises(RuntimeError):
        Settings(environment="production", _env_file=None).assert_safe_for_production()
    with pytest.raises(RuntimeError):
        Settings(
            environment="production", store_backend="memory",
            session_jwt_secret="x" * 40, _env_file=None,
        ).assert_safe_for_production()


def test_production_guard_requires_real_integration_configuration():
    settings = Settings(
        environment="production",
        store_backend="firestore",
        session_jwt_secret="a-strong-session-secret-with-more-than-32-bytes",
        gcp_project="gitresolve-5e43c",
        github_app_id=123,
        github_app_client_id="client-id",
        github_app_client_secret="client-secret",
        github_app_private_key="private-key",
        github_app_callback_url="https://api.example.test/api/v1/auth/github/callback",
        frontend_base_url="https://gitresolve.vercel.app",
        cors_origins=["https://gitresolve.vercel.app"],
        _env_file=None,
    )
    settings.assert_safe_for_production()

    with pytest.raises(RuntimeError, match="GitHub App"):
        Settings(
            environment="production",
            store_backend="firestore",
            session_jwt_secret="a-strong-session-secret-with-more-than-32-bytes",
            gcp_project="gitresolve-5e43c",
            _env_file=None,
        ).assert_safe_for_production()


def test_memory_transaction_is_atomic_and_create_is_exclusive():
    store = MemoryStore()
    store.set("a/1", {"v": 1})

    def bad(txn):
        txn.update("a/1", {"v": 2})
        txn.create("a/1", {"v": 3})  # already exists -> whole transaction fails

    with pytest.raises(AlreadyExists):
        store.run_transaction(bad)
    assert store.get("a/1") == {"v": 1}
