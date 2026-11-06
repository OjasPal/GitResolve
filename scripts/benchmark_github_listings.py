"""Read-only live listing benchmark; never prints credentials or repository contents."""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import Settings
from app.services.store.memory import MemoryStore
from app.services.github_real import RealGitHubGateway
from githubapp.timing import capture_timings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--github-login", required=True)
    args = parser.parse_args()
    settings = Settings()
    if not settings.local_state_path:
        raise SystemExit("Live benchmark requires configured local state with an authorized user.")
    # Read user identity only, without acquiring/writing the active state file.
    docs = json.loads(Path(settings.local_state_path).read_text(encoding="utf-8"))
    users = [(k.split("/", 1)[1], v) for k, v in docs.items()
             if k.startswith("users/") and k.count("/") == 1
             and v.get("githubLogin", "").casefold() == args.github_login.casefold()]
    if len(users) != 1:
        raise SystemExit("Requested GitHub identity must match exactly one local user.")
    uid, user = users[0]
    store = MemoryStore()
    store.set(f"users/{uid}", user)
    gateway = RealGitHubGateway(settings, store)
    results = []
    for iteration in range(2):
        for operation in ("repositories", "issues"):
            if operation == "issues" and not repos:
                continue
            start = time.perf_counter()
            with capture_timings() as timings:
                if operation == "repositories":
                    repos, _ = gateway.repository_page(uid)
                    count = len(repos)
                else:
                    issues, _ = gateway.issue_page(uid, repos[0].repo_id)
                    count = len(issues)
            results.append({"operation": operation, "pass": iteration + 1,
                            "duration_ms": round((time.perf_counter() - start) * 1000, 3),
                            "items": count, "spans": timings})
            Path(args.output).write_text(json.dumps(results, indent=2))
            print(operation, iteration + 1, results[-1]["duration_ms"], "ms", flush=True)

if __name__ == "__main__":
    main()
