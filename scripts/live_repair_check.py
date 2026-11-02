"""Explicit local diagnostic for an installed repo; never publishes without a diff hash."""
import argparse
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]
from app.core.config import Settings
from app.services.store.memory import MemoryStore
from app.services.task_service import TaskService
from app.services.github_real import RealGitHubGateway
from app.worker.runner import build_worker
from githubapp.client import GitHubClient

OUT = ROOT / ".diagnostics"
OUT.mkdir(exist_ok=True)
SNAPSHOT = OUT / "live-repair.json"
PROMPT = """Improve MCQGen's UI and output formatting using Streamlit and HTML/CSS only. Do not add React or Next.js.
Create a polished clean black glassmorphism theme. Improve the PDF/TXT upload area and MCQ generation inputs.
Display each question clearly with four neatly spaced, correctly parsed options, without raw messy text.
Add a Show answers checkbox, unchecked by default. Show each correct answer beside its question when enabled,
not as an answer list at the end. Toggling it must preserve generated questions without calling the model again.
Format the generated report with a clear heading. Preserve working functionality, add regression tests,
and prepare a pull request for human review. Do not merge automatically."""


def encode(value):
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    raise TypeError(type(value).__name__)


def decode(value):
    return datetime.fromisoformat(value["__datetime__"]) if set(value) == {"__datetime__"} else value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["plan", "execute", "publish"])
    parser.add_argument("--approved-diff-hash")
    args = parser.parse_args()
    settings = Settings()
    store = MemoryStore()
    if args.phase != "plan":
        store._docs = json.loads(SNAPSHOT.read_text(encoding="utf-8"), object_hook=decode)
    gateway = RealGitHubGateway(settings, store)
    service = TaskService(store, settings, gateway)
    if args.phase == "plan":
        installs = GitHubClient(gateway.auth._generate_jwt()).pages("/app/installations")
        installs = [i for i in installs if i["account"]["login"].lower() == "ojaspal"]
        identity = installs[0]["account"]
        uid = str(identity["id"])
        service.upsert_user(uid, identity["login"], installation_ids=[i["id"] for i in installs])
        repo = next(r for r in gateway.list_repositories(uid) if r.full_name.lower() == "ojaspal/mcqgen")
        task, _ = service.create_task(uid, repo.repo_id, None, None, prompt=PROMPT)
        # Explicit user approval applies ONLY to this task ID and repository.
        if task["repoFullName"].lower() != "ojaspal/mcqgen":
            raise SystemExit("The approved expanded budget applies only to OjasPal/MCQGen.")
        grant = {task["id"]: {"repo_url": "https://github.com/" + task["repoFullName"], "max_diff_lines": 400}}
        (OUT / "task_scope_grants.json").write_text(json.dumps(grant), encoding="utf-8")
        store.run_transaction(lambda tx: tx.update("tasks/" + task["id"], {"limits": {**task["limits"], "maxDiffLines": 400}}))
    else:
        task = next(doc for path, doc in store._docs.items() if path.startswith("tasks/") and path.count("/") == 1)
        uid = task["userId"]
        if args.phase == "execute":
            service.start_task(uid, task["id"])
        else:
            if not args.approved_diff_hash:
                raise SystemExit("Explicit reviewed diff hash required; refusing to publish.")
            service.approve_task(uid, task["id"], args.approved_diff_hash)
    worker = build_worker(settings, store)
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    cursor = 0
    while thread.is_alive():
        for event in store.list_events(task["id"], cursor, 100):
            print(event["stage"], event["message"], flush=True)
            cursor = event["seq"]
        with store._lock:
            SNAPSHOT.write_text(json.dumps(store._docs, default=encode), encoding="utf-8")
        time.sleep(2)
    thread.join()
    for event in store.list_events(task["id"], cursor, 100):
        print(event["stage"], event["message"], flush=True)
    SNAPSHOT.write_text(json.dumps(store._docs, default=encode), encoding="utf-8")
    task = store.get("tasks/" + task["id"])
    print("RESULT", task["status"], task.get("failure"), flush=True)
    if task.get("plan"):
        print("PLAN", json.dumps(task["plan"]), flush=True)
    artifact = store.get("tasks/" + task["id"] + "/artifacts/final_diff")
    if artifact:
        (OUT / "review.patch").write_text(artifact["content"], encoding="utf-8")
        print("REVIEW HASH", task["finalDiffHash"], flush=True)
    if task.get("pr"):
        print("PR", task["pr"]["url"], flush=True)


if __name__ == "__main__":
    main()
