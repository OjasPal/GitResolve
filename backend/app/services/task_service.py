"""API-side task operations. Never runs agent work; it only changes state and
marks tasks for dispatch (`dispatchPending`), which the worker picks up."""

from collections.abc import Callable
from datetime import datetime, time, timedelta, timezone
from typing import Any

from app.core.config import Settings
from app.core.errors import ApiError, invalid_state, not_found
from app.schemas.enums import (
    ApprovalState,
    DispatchPhase,
    EventLevel,
    EventType,
    TaskStage,
    TaskStatus,
)
from app.services.github import GitHubGateway, GitHubUnavailable, IssueInfo, RepositoryInfo
from app.services.ids import task_id_for
from app.services.lifecycle import PR_RETRY_STATUSES, is_active
from app.services.store.base import DELETE_FIELD, AlreadyExists, Doc, Store, Txn
from app.services.transitions import (
    EventSpec,
    active_slot_error,
    apply_change,
    artifact_path,
    build_event,
    event_path,
    read_ctx,
    task_path,
    user_path,
)

ISSUE_BODY_MAX_CHARS = 20_000
COMMENT_MAX_CHARS = 2_000


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_issue(info: IssueInfo) -> Doc:
    body = info.body or ""
    return {
        "number": info.number,
        "title": info.title,
        "url": info.url,
        "body": body[:ISSUE_BODY_MAX_CHARS],
        "bodyTruncated": len(body) > ISSUE_BODY_MAX_CHARS,
        "labels": list(info.labels)[:20],
        "comments": [
            {"author": c.get("author"), "body": str(c.get("body", ""))[:COMMENT_MAX_CHARS]}
            for c in info.comments[:10]
        ],
    }


class TaskService:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        github: GitHubGateway,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.store = store
        self.settings = settings
        self.github = github
        self.clock = clock

    # --- Users ----------------------------------------------------------------
    def upsert_user(
        self,
        user_id: str,
        github_login: str,
        avatar_url: str | None = None,
        installation_ids: list[int] | None = None,
    ) -> Doc:
        now = self.clock()

        def fn(txn: Txn) -> Doc:
            existing = txn.get(user_path(user_id))
            if existing is None:
                doc = {
                    "githubUserId": int(user_id),
                    "githubLogin": github_login,
                    "avatarUrl": avatar_url,
                    "installationIds": (installation_ids or [])[:50],
                    "activeTaskId": None,
                    "createdAt": now,
                    "lastLoginAt": now,
                    "schemaVersion": 1,
                }
                txn.create(user_path(user_id), doc)
                return doc
            changes = {"githubLogin": github_login, "avatarUrl": avatar_url, "lastLoginAt": now}
            if installation_ids is not None:
                changes["installationIds"] = installation_ids[:50]
            txn.update(user_path(user_id), changes)
            return {**existing, **changes}

        return self.store.run_transaction(fn)

    def get_user(self, user_id: str) -> Doc | None:
        return self.store.get(user_path(user_id))

    # --- Reads -----------------------------------------------------------------
    def get_task(self, user_id: str, task_id: str) -> Doc:
        """`get_task_for_user`: missing, expired and foreign tasks all raise 404."""
        task = self.store.get(task_path(task_id))
        if task is None or task["userId"] != user_id:
            raise not_found()
        if task.get("expiresAt") is not None and task["expiresAt"] <= self.clock():
            raise not_found()
        return task

    def list_tasks(self, user_id: str, limit: int, cursor: str | None) -> tuple[list[Doc], str | None]:
        rows = self.store.list_user_tasks(user_id, limit + 1, cursor)
        now = self.clock()
        rows = [r for r in rows if r.get("expiresAt") is None or r["expiresAt"] > now]
        page = rows[:limit]
        next_cursor = page[-1]["id"] if len(rows) > limit and page else None
        return page, next_cursor

    def list_events(self, user_id: str, task_id: str, after: int, limit: int) -> tuple[list[Doc], int]:
        self.get_task(user_id, task_id)
        rows = self.store.list_events(task_id, after, limit)
        now = self.clock()
        rows = [r for r in rows if r.get("expiresAt") is None or r["expiresAt"] > now]
        return rows, (rows[-1]["seq"] if rows else after)

    def list_artifacts(self, user_id: str, task_id: str) -> list[Doc]:
        self.get_task(user_id, task_id)
        now = self.clock()
        rows = [
            a
            for a in self.store.list_artifacts(task_id)
            if a.get("expiresAt") is None or a["expiresAt"] > now
        ]
        return sorted(rows, key=lambda a: a["id"])

    def get_artifact(self, user_id: str, task_id: str, artifact_id: str) -> Doc:
        self.get_task(user_id, task_id)
        art = self.store.get(artifact_path(task_id, artifact_id))
        if art is None or (art.get("expiresAt") is not None and art["expiresAt"] <= self.clock()):
            raise not_found()
        return art

    # --- Repository access (delegated to GitHub boundary) -----------------------
    def _github(self, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except GitHubUnavailable:
            raise ApiError(502, "github_unavailable", "GitHub is unavailable. Try again shortly.") from None

    def list_repositories(self, user_id: str) -> list[RepositoryInfo]:
        return self._github(lambda: self.github.list_repositories(user_id))

    def list_issues(self, user_id: str, repo_id: str) -> list[IssueInfo]:
        repo = self._github(lambda: self.github.get_repository(user_id, repo_id))
        if repo is None:
            raise not_found()
        return self._github(lambda: self.github.list_open_issues(user_id, repo_id))

    # --- Create -------------------------------------------------------------------
    def _replay(self, existing: Doc, user_id: str, repo_id: str, issue_number: int) -> Doc:
        if existing["userId"] != user_id:
            raise not_found()
        if existing["repoId"] != repo_id or existing["issue"]["number"] != issue_number:
            raise ApiError(
                409,
                "idempotency_key_reuse",
                "This Idempotency-Key was already used for a different request.",
            )
        return existing

    def create_task(
        self, user_id: str, repo_id: str, issue_number: int, idempotency_key: str | None
    ) -> tuple[Doc, bool]:
        """Returns (task, created). `created` is False for an idempotent replay."""
        task_id = task_id_for(user_id, idempotency_key)
        if idempotency_key:
            existing = self.store.get(task_path(task_id))
            if existing is not None:
                return self._replay(existing, user_id, repo_id, issue_number), False

        now = self.clock()
        midnight = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
        if self.store.count_user_tasks_since(user_id, midnight) >= self.settings.max_tasks_per_day:
            raise ApiError(
                429,
                "daily_limit_reached",
                "Daily task limit reached.",
                {"limit": self.settings.max_tasks_per_day, "resetsAt": (midnight + timedelta(days=1)).isoformat()},
            )

        repo = self._github(lambda: self.github.get_repository(user_id, repo_id))
        if repo is None:
            raise not_found()
        issue = self._github(lambda: self.github.get_issue(user_id, repo_id, issue_number))
        if issue is None:
            raise not_found()

        s = self.settings
        doc: Doc = {
            "id": task_id,
            "schemaVersion": 1,
            "userId": user_id,
            "repoId": repo_id,
            "repoFullName": repo.full_name,
            "installationId": repo.installation_id,
            "defaultBranch": repo.default_branch,
            "baseSha": None,
            "issue": normalize_issue(issue),
            "idempotencyKey": idempotency_key,
            "status": TaskStatus.QUEUED.value,
            "stage": TaskStage.INTAKE.value,
            "statusUpdatedAt": now,
            "createdAt": now,
            "updatedAt": now,
            "startedAt": None,
            "finishedAt": None,
            "dispatchPending": True,
            "dispatchPhase": DispatchPhase.PLAN.value,
            "dispatchAt": now,
            "cancelRequested": False,
            "plan": None,
            "testCommand": None,
            "baseline": None,
            "reproduction": None,
            "attempts": {"used": 0, "max": s.max_repair_attempts},
            "budgets": {"modelCalls": 0, "inputTokens": 0, "outputTokens": 0, "execSeconds": 0},
            "limits": {
                "maxRepairAttempts": s.max_repair_attempts,
                "maxModelCalls": s.max_model_calls,
                "taskTimeoutSeconds": s.task_timeout_seconds,
                "executionTimeoutSeconds": s.execution_timeout_seconds,
                "maxDiffFiles": s.max_diff_files,
                "maxDiffLines": s.max_diff_lines,
            },
            "finalDiffHash": None,
            "finalDiffSummary": None,
            "approval": None,
            "failure": None,
            "result": None,
            "pr": None,
            "deadlineAt": now + timedelta(seconds=s.task_timeout_seconds),
            "expiresAt": now + timedelta(days=s.task_retention_days),
            "eventSeq": 1,
        }
        first_event = build_event(
            doc, 1, TaskStage.INTAKE.value,
            EventSpec(EventType.STATUS_CHANGE, "Task queued.", data={"to": TaskStatus.QUEUED.value}),
            now, s,
        )

        repo_doc = {
            "installationId": repo.installation_id,
            "githubRepoId": int(repo.repo_id.split("_")[1]),
            "fullName": repo.full_name,
            "defaultBranch": repo.default_branch,
            "private": repo.private,
            "language": repo.language,
            "updatedAt": now,
            "schemaVersion": 1,
        }

        def fn(txn: Txn) -> tuple[Doc, bool]:
            existing = txn.get(task_path(task_id))
            if existing is not None:
                return self._replay(existing, user_id, repo_id, issue_number), False
            user = txn.get(user_path(user_id))
            holder_id = user.get("activeTaskId") if user else None
            if holder_id:
                holder = txn.get(task_path(holder_id))
                if holder is not None and is_active(holder["status"]):
                    raise active_slot_error(holder_id)
            txn.create(task_path(task_id), doc)
            txn.create(event_path(task_id, 1), first_event)
            if user is None:
                txn.create(
                    user_path(user_id),
                    {
                        "githubUserId": int(user_id),
                        "githubLogin": f"user-{user_id}",
                        "avatarUrl": None,
                        "installationIds": [],
                        "activeTaskId": task_id,
                        "createdAt": now,
                        "lastLoginAt": now,
                        "schemaVersion": 1,
                    },
                )
            else:
                txn.update(user_path(user_id), {"activeTaskId": task_id})
            return doc, True

        try:
            result = self.store.run_transaction(fn)
        except AlreadyExists:
            # Concurrent create with the same idempotency key lost the race.
            existing = self.store.get(task_path(task_id))
            if existing is None:
                raise
            return self._replay(existing, user_id, repo_id, issue_number), False
        self.store.set(f"repositories/{repo_id}", repo_doc)
        return result

    # --- User actions ---------------------------------------------------------------
    def start_task(self, user_id: str, task_id: str) -> Doc:
        now = self.clock()

        def fn(txn: Txn) -> Doc:
            ctx = read_ctx(txn, task_id, owner=user_id, now=now)
            if ctx.task["status"] != TaskStatus.AWAITING_START:
                raise invalid_state("Only a task awaiting start can be started.")
            return apply_change(
                txn, ctx, self.settings, now,
                to_status=TaskStatus.RUNNING,
                stage=TaskStage.SETUP,
                updates={
                    "dispatchPending": True,
                    "dispatchPhase": DispatchPhase.EXECUTE.value,
                    "dispatchAt": now,
                    "deadlineAt": now + timedelta(seconds=self.settings.task_timeout_seconds),
                    "cancelRequested": False,
                },
                events=[EventSpec(EventType.APPROVAL, "Plan approved; repair started.")],
            )

        return self.store.run_transaction(fn)

    def cancel_task(self, user_id: str, task_id: str) -> tuple[Doc, bool]:
        """Returns (task, cancelled_now). If False, cancellation was requested
        and the worker will stop at its next checkpoint."""
        now = self.clock()

        def fn(txn: Txn) -> tuple[Doc, bool]:
            ctx = read_ctx(txn, task_id, owner=user_id, now=now)
            task = ctx.task
            status = TaskStatus(task["status"])
            unclaimed = bool(task.get("dispatchPending"))
            if status in (TaskStatus.QUEUED, TaskStatus.AWAITING_START) or (
                status in (TaskStatus.PLANNING, TaskStatus.RUNNING) and unclaimed
            ):
                new = apply_change(
                    txn, ctx, self.settings, now,
                    to_status=TaskStatus.CANCELLED,
                    updates={"dispatchPending": False, "dispatchPhase": None},
                    events=[EventSpec(EventType.CANCELLATION, "Task cancelled by user.")],
                )
                return new, True
            if status in (TaskStatus.PLANNING, TaskStatus.RUNNING):
                if task.get("cancelRequested"):
                    return task, False
                new = apply_change(
                    txn, ctx, self.settings, now,
                    updates={"cancelRequested": True, "cancelRequestedAt": now},
                    events=[EventSpec(EventType.CANCELLATION, "Cancellation requested.")],
                )
                return new, False
            raise invalid_state(f"A task in {status.value} cannot be cancelled.")

        return self.store.run_transaction(fn)

    def approve_task(self, user_id: str, task_id: str, diff_hash: str) -> Doc:
        now = self.clock()

        def fn(txn: Txn) -> Doc:
            ctx = read_ctx(txn, task_id, owner=user_id, now=now)
            task = ctx.task
            status = TaskStatus(task["status"])
            phase = (task.get("failure") or {}).get("phase")
            retry = status in PR_RETRY_STATUSES and phase == DispatchPhase.PR.value
            if status != TaskStatus.AWAITING_APPROVAL and not retry:
                raise invalid_state("This task is not awaiting approval.")
            stored = task.get("finalDiffHash")
            if not stored or diff_hash != stored:
                raise ApiError(
                    409, "diff_hash_mismatch",
                    "The diff changed since you reviewed it. Reload and review again.",
                )
            return apply_change(
                txn, ctx, self.settings, now,
                to_status=TaskStatus.CREATING_PR,
                stage=TaskStage.PR,
                updates={
                    "approval": {
                        "state": ApprovalState.APPROVED.value,
                        "by": user_id,
                        "at": now,
                        "diffHash": diff_hash,
                    },
                    "dispatchPending": True,
                    "dispatchPhase": DispatchPhase.PR.value,
                    "dispatchAt": now,
                    "failure": None,
                    "finishedAt": None,
                    "cancelRequested": False,
                },
                events=[
                    EventSpec(
                        EventType.APPROVAL,
                        "Diff approved by user." if not retry else "PR creation retried by user.",
                        data={"diffHash": diff_hash},
                    )
                ],
            )

        return self.store.run_transaction(fn)

    def reject_task(self, user_id: str, task_id: str) -> Doc:
        now = self.clock()

        def fn(txn: Txn) -> Doc:
            ctx = read_ctx(txn, task_id, owner=user_id, now=now)
            if ctx.task["status"] != TaskStatus.AWAITING_APPROVAL:
                raise invalid_state("This task is not awaiting approval.")
            return apply_change(
                txn, ctx, self.settings, now,
                to_status=TaskStatus.REJECTED,
                updates={
                    "approval": {
                        "state": ApprovalState.REJECTED.value,
                        "by": user_id,
                        "at": now,
                        "diffHash": ctx.task.get("finalDiffHash"),
                    },
                    "dispatchPhase": None,
                },
                events=[EventSpec(EventType.APPROVAL, "Diff rejected by user.", level=EventLevel.INFO)],
            )

        return self.store.run_transaction(fn)
