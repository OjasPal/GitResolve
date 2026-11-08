"""Wheel-only preparation. Host pip only reads wheels, never installs packages."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid
from threading import RLock
from contextlib import contextmanager
import atexit
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from sandbox.dependencies import DependencyError, declared_requirements, requirement, marker_applies, wheel_metadata
from sandbox.process import capture, checkpoint, SandboxError

_environments = {}
_environment_lock = RLock()
ENVIRONMENT_CACHE_LIMIT = 8
ENVIRONMENT_CACHE_TTL = 1800


@contextmanager
def environment_lock(deadline, cancel):
    while not _environment_lock.acquire(timeout=0.1):
        checkpoint(deadline, cancel)
    try:
        checkpoint(deadline, cancel)
        yield
    finally:
        _environment_lock.release()


def clear_idle_environments(shutdown=False):
    from sandbox.process import _capture
    cleanup_capture = _capture if shutdown else capture
    with _environment_lock:
        for key, entry in list(_environments.items()):
            if entry['users'] == 0:
                try:
                    removed = cleanup_capture(['docker', 'volume', 'rm', '-f', entry['volume']], time.monotonic() + 10, limit=4096)
                    if removed.returncode == 0:
                        del _environments[key]
                except Exception:
                    pass


atexit.register(clear_idle_environments, shutdown=True)


def release_environment(volume):
    """Only drop the lease; execution containers always mount cached volumes read-only."""
    if not _environment_lock.acquire(timeout=10):
        raise SandboxError('cleanup_failed', 'Prepared environment lease could not be released.')
    try:
        for entry in _environments.values():
            if entry['volume'] == volume:
                entry['users'] -= 1
                return
    finally:
        _environment_lock.release()
    removed = capture(['docker', 'volume', 'rm', '-f', volume], time.monotonic() + 10, limit=4096)
    if removed.returncode:
        raise SandboxError('cleanup_failed', 'Prepared environment cleanup failed.')


def download_wheels(specs, destination, deadline, cancel=None):
    """Resolve metadata ourselves so pip never follows transitive direct URLs.

    Each round resolves all accumulated constraints with --no-deps. Wheels are
    only ZIP-read on the host. Reject incompatible graphs instead of executing
    builds or attempting unbounded dependency backtracking.
    """
    destination = Path(destination)
    requirements = {str(requirement(s)) for s in specs}
    for _ in range(12):
        checkpoint(deadline, cancel)
        active = [requirement(s) for s in sorted(requirements) if marker_applies(requirement(s))]
        if len(active) > 250:
            raise DependencyError("Dependency graph exceeds package limits.", "unsupported_dependency")
        extras = {}
        for req in active:
            extras.setdefault(canonicalize_name(req.name), set()).update(req.extras)
        with tempfile.TemporaryDirectory(prefix="gitresolve-wheel-round-") as temporary:
            env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}}
            env.update({"PIP_CONFIG_FILE": os.devnull, "PYTHONNOUSERSITE": "1", "NETRC": os.devnull, "HOME": temporary, "USERPROFILE": temporary})
            if not active:
                destination.mkdir(parents=True, exist_ok=True)
                return
            args = [sys.executable, "-I", "-m", "pip", "--isolated", "--disable-pip-version-check",
                    "download", "--no-deps", "--only-binary=:all:", "--cache-dir", str(Path(tempfile.gettempdir()) / "gitresolve-pip-downloads"), "--no-input", "--keyring-provider", "disabled",
                    "--index-url", "https://pypi.org/simple",
                    "--platform", "manylinux2014_x86_64",
                    "--platform", "manylinux_2_17_x86_64",
                    "--platform", "manylinux_2_28_x86_64",
                    "--implementation", "cp", "--python-version", "3.12", "--abi", "cp312",
                    "--dest", temporary, *[str(r).split(";", 1)[0].strip() for r in active]]
            downloaded = capture(args, deadline, cancel, 16000, env=env)
            if downloaded.returncode:
                text = (downloaded.stdout + downloaded.stderr).decode(errors="replace")
                if any(word in text for word in ("ConnectionError", "ReadTimeout", "Temporary failure", "ProxyError")):
                    raise SandboxError("wheel_download_unavailable", "The public wheel index is temporarily unavailable.")
                import re
                pkg_match = re.search(
                    r"(?:No matching distribution found for|Could not find a version that satisfies the requirement)\s+([A-Za-z0-9_.\-\[\]=<>!~]+)",
                    text,
                    re.IGNORECASE,
                )
                pkg_hint = f" ({pkg_match.group(1)})" if pkg_match else ""
                raise DependencyError(
                    f"Compatible binary wheels could not be resolved{pkg_hint}. Source builds are disabled; provide wheel-supported versions.",
                    "unsupported_dependency",
                )
            wheels = sorted(Path(temporary).glob("*.whl"))
            if sum(p.stat().st_size for p in wheels) > 512_000_000:
                raise DependencyError("Wheelhouse exceeds the download size limit.", "unsupported_dependency")
            new = set(requirements)
            for wheel in wheels:
                name, version, dependencies = wheel_metadata(wheel)
                for dep in dependencies:
                    if marker_applies(dep, extras.get(name, ())):
                        # Remove markers only AFTER evaluating them for the target,
                        # so pip cannot reevaluate Linux markers against Windows.
                        new.add(str(dep).split(";", 1)[0].strip())
            if new == requirements:
                destination.mkdir(parents=True, exist_ok=True)
                for wheel in wheels:
                    shutil.copyfile(wheel, destination / wheel.name)
                return
            requirements = new
    raise DependencyError("Dependency resolution exceeded the bounded round limit.", "unsupported_dependency")


def prepare_environment(repo, profile, image, workspace, deadline, cancel=None):
    specs, dependency_hash = declared_requirements(repo, profile)
    version = profile.python_version
    if version:
        supported = "3.12" == version or "3.12.0" == version
        if any(c in version for c in "<>=!~"):
            supported = SpecifierSet(version).contains("3.12.0")
        if not supported:
            raise DependencyError("This sandbox supports Python 3.12; select a compatible repository version.", "unsupported_dependency")
    if not specs:
        return None
    inspected = capture(["docker", "image", "inspect", "--format", "{{.Id}}", image], deadline, cancel, 4096)
    if inspected.returncode:
        raise SandboxError("preparation_failed", "The reviewed sandbox image is unavailable.")
    digest = inspected.stdout.decode().strip()
    cache_key = hashlib.sha256((dependency_hash + "|3.12|" + digest).encode()).hexdigest()
    wheelhouse = cached_wheels(specs, cache_key, deadline, cancel)
    with environment_lock(deadline, cancel):
        for key, entry in list(_environments.items()):
            if entry['users'] == 0 and entry['expires'] <= time.monotonic():
                removed = capture(['docker', 'volume', 'rm', '-f', entry['volume']], deadline, cancel, 4096)
                if removed.returncode:
                    raise SandboxError('cleanup_failed', 'Expired environment cleanup failed.')
                del _environments[key]
        entry = _environments.get(cache_key)
        if entry is not None:
            present = capture(['docker', 'volume', 'inspect', entry['volume']], deadline, cancel, 4096)
            if present.returncode == 0:
                entry['users'] += 1
                return entry['volume']
            del _environments[cache_key]
        volume = _build_environment(wheelhouse, image, deadline, cancel)
        if len(_environments) < ENVIRONMENT_CACHE_LIMIT:
            _environments[cache_key] = {'volume': volume, 'users': 1,
                                       'expires': time.monotonic() + ENVIRONMENT_CACHE_TTL}
        # At capacity this is an ephemeral volume; release_environment removes it.
        return volume


def _build_environment(wheelhouse, image, deadline, cancel):
    prepared = "gitresolve-env-" + uuid.uuid4().hex[:12]
    success = False
    container = "gitresolve-prepare-" + uuid.uuid4().hex[:12]
    args = ["docker", "run", "--name", container, "--pull", "never", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "2g", "--memory-swap", "2g",
            "--cpus", "2", "--pids-limit", "256", "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m",
            "--mount", f"type=bind,source={wheelhouse},target=/wheelhouse,readonly",
            "--mount", f"type=volume,source={prepared},target=/opt/prepared",
            "--user", "0:0", image, "python", "-I", "-c",
            "import subprocess; subprocess.run(['python','-I','-m','venv','--copies','--system-site-packages','/opt/prepared'],check=True); "
            "subprocess.run(['/opt/prepared/bin/python','-I','-m','pip','--isolated','install','--no-index','--no-deps','--no-compile','--find-links=/wheelhouse',*__import__('glob').glob('/wheelhouse/*.whl')],check=True)"]
    try:
        built = capture(args, deadline, cancel, 16000)
        if built.returncode:
            raise DependencyError("Offline wheel installation failed in the disposable container.", "unsupported_dependency")
        success = True
    finally:
        removed = capture(["docker", "rm", "-f", container], time.monotonic() + 10, limit=4096)
        if not success:
            capture(["docker", "volume", "rm", "-f", prepared], time.monotonic() + 10, limit=4096)
        if removed.returncode:
            raise SandboxError("cleanup_failed", "Environment preparation container cleanup failed.")
    return prepared


def cached_wheels(specs, key, deadline, cancel=None, cache_root=None):
    """Publish complete immutable wheel sets atomically; failed rounds aren't cached."""
    root = Path(cache_root) if cache_root else Path(tempfile.gettempdir()) / "gitresolve-wheel-cache-v1"
    root.mkdir(parents=True, exist_ok=True)
    target = root / key
    def valid():
        try:
            manifest = json.loads((target / "manifest.json").read_text())
            if manifest.get("key") != key:
                return False
            files = manifest["files"]
            if set(files) != {p.name for p in target.glob("*.whl")}:
                return False
            return all(Path(name).name == name and not (target / name).is_symlink()
                       and hashlib.sha256((target / name).read_bytes()).hexdigest() == digest
                       for name, digest in files.items())
        except (OSError, ValueError, KeyError, TypeError):
            return False
    if valid():
        return target
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=root) as temp:
        stage = Path(temp) / "entry"
        download_wheels(specs, stage, deadline, cancel)
        files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in stage.glob("*.whl")}
        (stage / "manifest.json").write_text(json.dumps({"key": key, "files": files}))
        if target.exists():
            if valid():
                return target
            raise SandboxError("wheel_cache_invalid", "The wheel cache failed integrity checks; remove the damaged cache entry and retry.")
        try:
            stage.rename(target)
        except OSError:
            if not valid():
                raise
    return target
