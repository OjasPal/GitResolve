"""Guards against drift between the deployment files and the application settings."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core.config import Settings

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
SECRET_ENV = {
    "SESSION_JWT_SECRET": "a-strong-session-secret-with-more-than-32-bytes",
    "GITHUB_APP_CLIENT_SECRET": "client-secret",
    "GITHUB_APP_PRIVATE_KEY": "private-key",
}


def parse_env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        match = re.fullmatch(r"([A-Z_]+):\s*(['\"])(.*)\2\s*(#.*)?", line.strip())
        if match:
            values[match.group(1)] = match.group(3)
    return values


def test_example_env_only_sets_known_settings_and_no_secrets():
    values = parse_env_file(DEPLOY / "env.production.yaml.example")
    fields = {name.upper() for name in Settings.model_fields}
    assert set(values) <= fields
    assert not set(values) & set(SECRET_ENV)  # secrets come from Secret Manager only


def test_example_env_plus_secrets_forms_a_valid_production_configuration():
    values = parse_env_file(DEPLOY / "env.production.yaml.example")
    values["GITHUB_APP_CALLBACK_URL"] = "https://gitresolve-api-1.asia-south1.run.app/api/v1/auth/github/callback"
    merged = {**values, **SECRET_ENV}
    # Build through the environment, exactly as Cloud Run does.
    import os

    old = dict(os.environ)
    try:
        os.environ.update(merged)
        settings = Settings(_env_file=None)
    finally:
        os.environ.clear()
        os.environ.update(old)
    settings.assert_safe_for_production()
    assert settings.cors_origins == ["https://gitresolve.vercel.app"]
    assert settings.trusted_proxy_hops == 1 and settings.docs_on is False and settings.log_json is True


def test_deploy_scripts_wire_every_secret_the_api_needs():
    for script in ("deploy.sh", "deploy.ps1"):
        text = (DEPLOY / script).read_text()
        for env_name, secret in (
            ("SESSION_JWT_SECRET", "session-jwt-secret"),
            ("GITHUB_APP_CLIENT_SECRET", "github-app-client-secret"),
            ("GITHUB_APP_PRIVATE_KEY", "github-app-private-key"),
        ):
            assert f"{env_name}={secret}:latest" in text, (script, env_name)
        assert "--min-instances 0 --max-instances 2 --memory 512Mi" in text and "--timeout 60" in text
    for script in ("setup.sh", "setup.ps1"):
        text = (DEPLOY / script).read_text()
        assert "gemini-api-key" in text and "roles/secretmanager.secretAccessor" in text


def test_api_service_account_is_not_granted_the_llm_key():
    sh = (DEPLOY / "setup.sh").read_text()
    api_line = next(line for line in sh.splitlines() if line.startswith("API_SECRETS="))
    assert "gemini" not in api_line


def test_firestore_rules_deny_all_client_access():
    rules = (DEPLOY / "firestore.rules").read_text()
    assert "allow read, write: if false" in rules and "if true" not in rules


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_bash_scripts_have_valid_syntax():
    for script in ("setup.sh", "deploy.sh"):
        result = subprocess.run(
            [shutil.which("bash"), "-n"], input=(DEPLOY / script).read_text().encode(),
            capture_output=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_docker_copy_sources_exist_in_backend_context():
    backend = DEPLOY.parent
    for line in (backend / "Dockerfile").read_text().splitlines():
        if line.startswith("COPY "):
            for source in line.split()[1:-1]:
                assert (backend / source).exists(), source


def test_worker_production_check_does_not_need_session_or_oauth_secrets():
    ok = Settings(
        environment="production", store_backend="firestore", gcp_project="p",
        github_app_id=1, github_app_private_key="k", _env_file=None,
    )
    ok.assert_safe_for_worker()
    with pytest.raises(RuntimeError):
        Settings(environment="production", store_backend="memory", _env_file=None).assert_safe_for_worker()
    with pytest.raises(RuntimeError):
        Settings(environment="production", store_backend="firestore", gcp_project="p", _env_file=None).assert_safe_for_worker()
