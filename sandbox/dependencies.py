"""Static requirement parsing and wheel metadata validation; no package execution."""
from dataclasses import dataclass
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import re
import tomllib
import zipfile
from packaging.requirements import Requirement, InvalidRequirement
from packaging.markers import default_environment
from packaging.utils import canonicalize_name
from sandbox.process import SandboxError

class DependencyError(SandboxError):
    def __init__(self, message, code="invalid_dependency_spec"):
        super().__init__(code, message)


def requirement(text):
    if not isinstance(text, str) or len(text) > 1000 or any(c in text for c in "\r\n\x00") or text.startswith("-"):
        raise DependencyError("Dependency options, includes, and executable specifications are not supported.")
    try:
        parsed = Requirement(text)
    except InvalidRequirement:
        raise DependencyError("A declared dependency is not a valid package requirement.") from None
    if parsed.url:
        raise DependencyError("URL, VCS and local dependencies are unsupported. Declare public wheel packages instead.")
    return parsed


def declared_requirements(root, profile):
    root = Path(root).resolve()
    visited, specs = set(), []
    def read_file(path):
        path = Path(path)
        if path.is_symlink() or not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in path.parents if p != root.parent):
            raise DependencyError("Requirement includes must stay inside the repository without symlinks.")
        path = path.resolve()
        if path in visited:
            return
        visited.add(path)
        if len(visited) > 32 or not path.is_file() or path.stat().st_size > 100000:
            raise DependencyError("Dependency file exceeds the supported inspection limits.")
        for line in path.read_text(encoding="utf-8").splitlines():
            line = re.split(r"\s+#", line, 1)[0].strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("-r ") or line.startswith("--requirement "):
                read_file(path.parent / line.split(maxsplit=1)[1])
            else:
                specs.append(str(requirement(line)))
    # Runtime and explicitly declared test requirements are included; arbitrary
    # deployment/dev files are inventoried, not silently combined.
    for name in ("requirements.txt", "requirements-test.txt", "requirements-tests.txt"):
        if (root / name).exists():
            read_file(root / name)
    project = root / "pyproject.toml"
    if project.exists():
        if project.is_symlink() or not project.resolve().is_relative_to(root):
            raise DependencyError("Project dependency files cannot be symlinks.")
        try:
            data = tomllib.loads(project.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError):
            raise DependencyError("The pyproject dependency declaration is invalid.") from None
        meta = data.get("project", {})
        if "dependencies" in meta.get("dynamic", []):
            raise DependencyError("Dynamic dependency discovery would execute repository code and is unsupported.")
        if "tool" in data and "poetry" in data["tool"] and not meta:
            raise DependencyError("Export Poetry dependencies to standard requirements or PEP 621 before running.")
        specs.extend(str(requirement(v)) for v in meta.get("dependencies", []))
        for group in ("test", "tests"):
            specs.extend(str(requirement(v)) for v in meta.get("optional-dependencies", {}).get(group, []))
        visited.add(project)
    if not specs and getattr(profile, "dependency_files", None):
        for rel_dep in sorted(profile.dependency_files):
            dep_name = Path(rel_dep).name
            if dep_name in {"requirements.txt", "requirements-test.txt", "requirements-tests.txt"}:
                sub_req = root / rel_dep
                if sub_req.exists():
                    read_file(sub_req)
    if not specs and any(Path(n).name in {"setup.py", "setup.cfg", "Pipfile", "Pipfile.lock", "poetry.lock"} for n in profile.dependency_files):
        raise DependencyError("This dependency format requires a static requirements export; repository build scripts will not run.")
    canon_names = {canonicalize_name(requirement(s).name) for s in specs}
    auto_extras: list[str] = []
    if "pydantic" in canon_names and "email-validator" not in canon_names:
        uses_email = getattr(profile, "uses_pydantic_email", False)
        if not uses_email:
            for py_file in root.rglob("*.py"):
                try:
                    rel_parts = py_file.relative_to(root).parts
                except ValueError:
                    continue
                if any(p in {".git", ".venv", "venv", "node_modules", "__pycache__"} for p in rel_parts) or py_file.is_symlink():
                    continue
                try:
                    txt = py_file.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if "EmailStr" in txt or "NameEmail" in txt or "email_validator" in txt:
                    uses_email = True
                    break
        if uses_email:
            req_str = str(requirement("email-validator>=2.0.0"))
            specs.append(req_str)
            auto_extras.append(req_str)
    if "passlib" in canon_names:
        has_bcrypt_pin = any(
            canonicalize_name(requirement(s).name) == "bcrypt" and bool(requirement(s).specifier)
            for s in specs
        )
        if not has_bcrypt_pin:
            specs = [s for s in specs if canonicalize_name(requirement(s).name) != "bcrypt"]
            req_str = str(requirement("bcrypt==4.0.1"))
            specs.append(req_str)
            auto_extras.append(req_str)
    digest = hashlib.sha256()
    for name in sorted(set(profile.dependency_files) | {p.relative_to(root).as_posix() for p in visited}):
        path = root / name
        digest.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
    for extra_spec in sorted(auto_extras):
        digest.update(f"__auto_extra__:{extra_spec}\0".encode())
    return sorted(set(specs)), digest.hexdigest()


def marker_applies(req, extras=()):
    env = {**default_environment(), "python_version": "3.12", "python_full_version": "3.12.0",
           "sys_platform": "linux", "platform_system": "Linux", "os_name": "posix",
           "platform_machine": "x86_64", "implementation_name": "cpython"}
    return req.marker is None or any(req.marker.evaluate({**env, "extra": extra}) for extra in ("", *extras))


def wheel_metadata(path):
    with zipfile.ZipFile(path) as wheel:
        members = wheel.infolist()
        if len(members) > 100000 or sum(m.file_size for m in members) > 1_000_000_000:
            raise DependencyError("Wheel exceeds preparation size limits.", "unsupported_dependency")
        metadata = [m for m in members if m.filename.endswith(".dist-info/METADATA")]
        if len(metadata) != 1 or metadata[0].file_size > 2_000_000:
            raise DependencyError("Wheel metadata is missing or too large.", "unsupported_dependency")
        for member in members:
            name = member.filename
            if name.startswith(("/", "\\")) or ".." in Path(name).parts or "\\" in name:
                raise DependencyError("Wheel contains an unsafe member path.", "unsupported_dependency")
        info = BytesParser().parsebytes(wheel.read(metadata[0]))
        dependencies = [requirement(v) for v in info.get_all("Requires-Dist", [])]
        return canonicalize_name(info["Name"]), info["Version"], dependencies
