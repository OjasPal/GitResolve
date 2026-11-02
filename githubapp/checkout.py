"""Bounded private archive acquisition; no credentials cross the container boundary."""
import re
import time
from urllib.parse import urlsplit
import requests
from githubapp.client import API, GitHubClientError


def acquire_archive(client, full_name, sha, workspace, deadline, cancel):
    from sandbox.process import checkpoint
    for attempt in range(2):
        try:
            return _acquire_archive_once(client, full_name, sha, workspace, deadline, cancel)
        except requests.RequestException:
            if attempt == 1:
                raise GitHubClientError("github_error") from None
            # Only retry a failed read. Never retry rejected redirects or permissions.
            (workspace / "source.tar.gz").unlink(missing_ok=True)
            checkpoint(deadline, cancel)


def _acquire_archive_once(client, full_name, sha, workspace, deadline, cancel):
    from sandbox.acquisition import ARCHIVE_LIMIT, extract_archive
    from sandbox.process import checkpoint, SandboxError
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise GitHubClientError("invalid_commit")
    archive = workspace / "source.tar.gz"
    try:
        checkpoint(deadline, cancel)
        # GitHub's redirect is pre-authorized. Never forward the installation token.
        with requests.get(f"{API}/repos/{full_name}/tarball/{sha}", headers=client._headers,
                          timeout=(5, max(1, min(20, deadline - time.monotonic()))), allow_redirects=False, stream=True) as response:
            if response.status_code != 302:
                raise GitHubClientError("github_error")
            url = response.headers.get("Location", "")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "codeload.github.com" or parsed.username or parsed.fragment:
            raise GitHubClientError("github_error")
        with requests.Session() as session:
            session.trust_env = False
            with session.get(url, timeout=(5, max(1, min(20, deadline - time.monotonic()))), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise GitHubClientError("github_error")
                total = 0
                with archive.open("xb") as output:
                    for chunk in response.iter_content(65536):
                        checkpoint(deadline, cancel)
                        total += len(chunk)
                        if total > ARCHIVE_LIMIT:
                            raise SandboxError("repository_too_large", "Repository archive exceeds limit.")
                        output.write(chunk)
        with archive.open("rb") as stream:
            return extract_archive(stream, workspace / "repo", deadline, cancel)
    except requests.RequestException:
        raise
