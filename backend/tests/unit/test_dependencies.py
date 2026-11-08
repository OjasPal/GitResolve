from pathlib import Path
import zipfile
import pytest
from sandbox.dependencies import DependencyError, requirement, declared_requirements, wheel_metadata
from sandbox.profiler import profile_repository

@pytest.mark.parametrize("text", ["-e .", "--index-url https://evil.invalid", "--extra-index-url https://evil.invalid", "git+https://example.invalid/a", "thing @ https://example.invalid/a.whl", "../local", "thing @ file:///private"])
def test_rejects_executable_or_remote_spec(text):
    with pytest.raises(DependencyError): requirement(text)


def test_requirements_include_cannot_escape(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "requirements.txt").write_text("-r ../outside.txt")
    (tmp_path / "outside.txt").write_text("packaging")
    with pytest.raises(DependencyError): declared_requirements(root, profile_repository(root))


def test_static_pyproject_does_not_invoke_build_backend(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[build-system]\nrequires=["evil-backend"]\nbuild-backend="evil"\n[project]\nname="fixture"\ndependencies=["packaging==24.2"]')
    specs, digest = declared_requirements(tmp_path, profile_repository(tmp_path))
    assert specs == ["packaging==24.2"] and len(digest) == 64


def test_transitive_direct_url_rejected_before_pip_sees_it(tmp_path):
    wheel = tmp_path / "bad.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("bad-1.dist-info/METADATA", "Name: bad\nVersion: 1\nRequires-Dist: evil @ https://example.invalid/evil.whl\n")
    with pytest.raises(DependencyError): wheel_metadata(wheel)


def test_cache_reuse_and_corruption(tmp_path, monkeypatch):
    from sandbox.environment import cached_wheels
    from sandbox.process import SandboxError
    calls = []
    def download(specs, destination, *args):
        calls.append(1)
        destination.mkdir()
        (destination / "example.whl").write_bytes(b"wheel-data")
    monkeypatch.setattr("sandbox.environment.download_wheels", download)
    path = cached_wheels(["example"], "a" * 64, 9999999999, cache_root=tmp_path)
    assert cached_wheels(["example"], "a" * 64, 9999999999, cache_root=tmp_path) == path
    assert len(calls) == 1
    (path / "example.whl").write_bytes(b"corrupt")
    with pytest.raises(SandboxError):
        cached_wheels(["example"], "a" * 64, 9999999999, cache_root=tmp_path)
