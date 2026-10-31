"""Acquire a public pinned GitHub archive without host checkout or credentials."""
import io
import gzip
import os
import re
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

from sandbox.process import SandboxError, capture, checkpoint

ARCHIVE_LIMIT = 32 * 1024 * 1024
EXPANDED_LIMIT = 256 * 1024 * 1024
FILE_LIMIT = 10_000
GITHUB_URL = re.compile(r"https://github\.com/([A-Za-z0-9_-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise SandboxError("redirect_rejected", "Repository redirects are not allowed.")


def extract_archive(stream, target, deadline, cancel, strip_root=True):
    """Extract regular files manually; never use tar.extract or trust link paths."""
    total = 0
    seen = set()
    root = None
    target.mkdir()
    target.chmod(0o755)
    signature = stream.read(2)
    stream.seek(0)
    source_stream = gzip.GzipFile(fileobj=stream) if signature == b"\x1f\x8b" else stream
    class Limited:
        used = 0
        def read(self, size=-1):
            checkpoint(deadline, cancel)
            available = EXPANDED_LIMIT + 8 * 1024 * 1024 - self.used
            data = source_stream.read(min(size if size >= 0 else available + 1, available + 1))
            self.used += len(data)
            if self.used > EXPANDED_LIMIT + 8 * 1024 * 1024:
                raise SandboxError("repository_too_large", "Decompressed archive exceeds its limit.")
            return data
    with tarfile.open(fileobj=Limited(), mode="r|") as archive:
        for count, member in enumerate(archive, 1):
            checkpoint(deadline, cancel)
            if count > FILE_LIMIT:
                raise SandboxError("repository_too_large", "Too many archive entries.")
            name = member.name
            parts = PurePosixPath(name).parts
            if (not parts or name.startswith("/") or "\\" in name or ":" in name
                    or any(p == ".." or p.casefold() == ".git" or p.endswith((".", " ")) for p in parts)
                    or any(re.fullmatch(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?", p) for p in parts)
                    or not (member.isdir() or member.isfile()) or member.issparse()):
                raise SandboxError("unsafe_path", "Unsafe archive entry.")
            if strip_root:
                root = root or parts[0]
                if parts[0] != root:
                    raise SandboxError("unsafe_path", "Archive has multiple roots.")
                parts = parts[1:]
            if not parts:
                continue
            key = "/".join(parts).casefold()
            if key in seen:
                raise SandboxError("unsafe_path", "Duplicate archive path.")
            seen.add(key)
            destination = target.joinpath(*parts)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                for directory in (destination, *destination.parents):
                    if directory == target.parent:
                        break
                    directory.chmod(0o755)
                continue
            total += member.size
            if member.size < 0 or total > EXPANDED_LIMIT:
                raise SandboxError("repository_too_large", "Expanded repository exceeds its limit.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            for directory in (destination.parent, *destination.parent.parents):
                if directory == target.parent:
                    break
                directory.chmod(0o755)
            source = archive.extractfile(member)
            remaining = member.size
            with destination.open("xb") as output:
                while remaining:
                    checkpoint(deadline, cancel)
                    chunk = source.read(min(65536, remaining))
                    if not chunk:
                        raise SandboxError("archive_failed", "Truncated repository archive.")
                    output.write(chunk)
                    remaining -= len(chunk)
            destination.chmod(0o755 if member.mode & 0o111 else 0o644)
    return target


def acquire(request, workspace, deadline, cancel, allow_local_repo=False):
    match = GITHUB_URL.fullmatch(request.repo_url)
    if match:
        owner, repo = match.groups()
        if repo in {".", ".."}:
            raise SandboxError("invalid_repo_url", "Invalid repository name.")
        url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/{request.commit_sha}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        archive_path = workspace / "source.tar.gz"
        with opener.open(url, timeout=5) as response, archive_path.open("xb") as output:
            total = 0
            while True:
                checkpoint(deadline, cancel)
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > ARCHIVE_LIMIT:
                    raise SandboxError("repository_too_large", "Repository download exceeds its limit.")
                output.write(chunk)
        with archive_path.open("rb") as source:
            return extract_archive(source, workspace / "repo", deadline, cancel)
    if not allow_local_repo or not Path(request.repo_url).is_dir():
        raise SandboxError("invalid_repo_url", "Only public GitHub HTTPS repositories are supported.")
    # Test-only local source: archive objects, never checkout or run hooks/filters.
    env = {k: v for k, v in os.environ.items() if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT"}}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    result = capture(["git", "-C", request.repo_url, "archive", "--format=tar", request.commit_sha],
                     deadline, cancel, ARCHIVE_LIMIT, env)
    if result.returncode:
        raise SandboxError("checkout_failed", "Pinned local commit could not be archived.")
    if result.stdout_truncated or result.stderr_truncated:
        raise SandboxError("repository_too_large", "Local archive exceeds its limit.")
    return extract_archive(io.BytesIO(result.stdout), workspace / "repo", deadline, cancel, strip_root=False)
