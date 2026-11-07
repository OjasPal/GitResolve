"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { toast } from "sonner";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { TaskResponse } from "@/types";
import { approveTask, cancelTask, createTask, rejectTask, startTask } from "@/lib/api";
import { AlertTriangle, GitPullRequest, Loader2, Play, RefreshCw, XCircle } from "lucide-react";

interface ActionButtonsProps {
  task: TaskResponse;
  diffText?: string;
  planEditing?: boolean;
  reviewedDiffHash: string | null;
  onReviewDiff: () => void;
  onActionSuccess: (updatedTask: TaskResponse) => void;
}

export function ActionButtons({ task, diffText, planEditing = false, reviewedDiffHash, onReviewDiff, onActionSuccess }: ActionButtonsProps) {
  const router = useRouter();
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [loadingAction, setLoadingAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const diffLines = diffText?.split("\n");
  const added = diffLines?.filter(line => line.startsWith("+") && !line.startsWith("+++")).length;
  const removed = diffLines?.filter(line => line.startsWith("-") && !line.startsWith("---")).length;
  const actions = task.availableActions || [];
  const canApprove = !planEditing && Boolean(task.finalDiffHash && reviewedDiffHash === task.finalDiffHash);
  const needsDiffReview = (actions.includes("approve") || actions.includes("retry_pr")) && !canApprove;

  const handleStart = async () => {
    if (planEditing) return;
    try {
      setLoadingAction("start");
      setError(null);
      const updated = await startTask(task.id, task.gateRevision || 0);
      onActionSuccess(updated);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to start repair");
    } finally {
      setLoadingAction(null);
    }
  };

  const handleApprove = async () => {
    if (!task.finalDiffHash || reviewedDiffHash !== task.finalDiffHash) {
      setError("Open and review the complete current diff before approving.");
      return;
    }
    try {
      setLoadingAction("approve");
      setError(null);
      const updated = await approveTask(task.id, task.finalDiffHash, task.gateRevision || 0);
      onActionSuccess(updated);
      setApprovalOpen(false);
      toast.success("Approved. Creating your draft pull request.");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to approve diff");
    } finally {
      setLoadingAction(null);
    }
  };

  const handleReject = async () => {
    if (!confirm("Are you sure you want to reject this proposed diff? This will terminate the task without creating a PR.")) {
      return;
    }
    try {
      setLoadingAction("reject");
      setError(null);
      const updated = await rejectTask(task.id);
      onActionSuccess(updated);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to reject task");
    } finally {
      setLoadingAction(null);
    }
  };

  const handleCancel = async () => {
    if (!confirm("Are you sure you want to cancel this task?")) {
      return;
    }
    try {
      setLoadingAction("cancel");
      setError(null);
      const updated = await cancelTask(task.id);
      onActionSuccess(updated);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to cancel task");
    } finally {
      setLoadingAction(null);
    }
  };

  const handleRunAgain = () => {
    router.push(`/tasks/new?repo=${encodeURIComponent(task.repoId)}`);
  };

  const handleRecheck = async () => {
    try {
      setLoadingAction("recheck");
      setError(null);
      const source =
        typeof task.issue.number === "number"
          ? task.issue.number
          : task.issue.body || task.issue.title || "Re-check after external setup";
      const created = await createTask(task.repoId, source, `recheck-${task.id}-${Date.now()}`);
      router.push(`/tasks/${created.id}`);
    } catch {
      router.push(`/tasks/new?repo=${encodeURIComponent(task.repoId)}`);
    } finally {
      setLoadingAction(null);
    }
  };

  if (task.status === "completed") {
    return task.pr?.url ? <a href={task.pr.url} target="_blank" rel="noopener noreferrer" className="button primary"><GitPullRequest className="w-4 h-4"/>View Pull Request</a> : null;
  }

  if (actions.length === 0) {
    if (task.status === "creating_pr") {
      return (
        <div className="flex items-center gap-2 text-neutral-400 text-sm font-medium">
          <Loader2 className="w-4 h-4 animate-spin" />
          <span>Opening Pull Request on GitHub...</span>
        </div>
      );
    }
    return null;
  }

  return (
    <div className="flex flex-col gap-2">
      {needsDiffReview && (
        <div className="max-w-sm space-y-2 text-xs" role="status">
          <p className="text-neutral-500 dark:text-neutral-400">
            The repair is ready. Open the complete proposed changes to enable approval.
          </p>
          <button type="button" onClick={onReviewDiff} className="font-medium text-neutral-600 dark:text-neutral-400 underline underline-offset-4 cursor-pointer">
            Review proposed changes
          </button>
        </div>
      )}
      {error && (
        <div className="button">
          <AlertTriangle className="w-4 h-4 shrink-0 text-rose-400" />
          <span>{error}</span>
        </div>
      )}

      <Dialog.Root open={approvalOpen} onOpenChange={setApprovalOpen}><Dialog.Portal><Dialog.Overlay className="modal-scrim"/><Dialog.Content className="approval-modal" data-lenis-prevent><Dialog.Title>Publish this exact fix?</Dialog.Title><Dialog.Description>Approve the reviewed patch and its recorded evidence. GitResolve will create a branch and a draft pull request. It will never merge it.</Dialog.Description><div className="approval-facts"><div><strong>{task.finalDiffSummary?.filesChanged ?? "—"}</strong>files changed</div><div><strong>+{added ?? task.finalDiffSummary?.linesAdded ?? "—"}</strong>lines added</div><div><strong>−{removed ?? task.finalDiffSummary?.linesRemoved ?? "—"}</strong>lines removed</div></div><p><strong>{task.finalDiffSummary?.commitMessage || "Reviewed repair"}</strong></p><p>Repository: {task.repoFullName}. A unique gitresolve-agent branch is reserved at publication. Test evidence is available in the Tests tab; UI design still needs your visual review.</p>{task.finalDiffSummary?.setupNote && <div className="p-3 rounded-lg border border-neutral-300 dark:border-neutral-700 bg-neutral-50 dark:bg-neutral-900 text-xs space-y-1"><strong>Setup you must do (included in PR body)</strong><p className="whitespace-pre-wrap">{task.finalDiffSummary.setupNote}</p></div>}<code>{task.finalDiffHash}</code>{error && <p className="inline-error" role="alert">{error}</p>}<div className="actions"><Dialog.Close className="button" disabled={!!loadingAction}>Keep reviewing</Dialog.Close><button className="button primary" onClick={handleApprove} disabled={!!loadingAction || !canApprove}>{loadingAction ? "Creating…" : "Approve & create draft PR"}</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>
      <div className="flex flex-wrap items-center gap-2.5">
        {/* Gate 1: Start Repair Action */}
        {actions.includes("start") && (
          <button
            onClick={handleStart}
            disabled={!!loadingAction || planEditing}
            title={planEditing ? "Finish your conversation or close the request editor before starting repair" : undefined}
            className="button primary"
          >
            {loadingAction === "start" ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Play className="w-4 h-4 fill-current" />
            )}
            <span>Start repair</span>
          </button>
        )}

        {/* Gate 2: Approve & Create PR Action */}
        {actions.includes("approve") && (
          <button
            onClick={() => setApprovalOpen(true)}
            disabled={!!loadingAction || !canApprove}
            title={!canApprove ? "Open the complete current diff before approving" : "Approve these reviewed changes and create a draft PR"}
            className="button primary"
          >
            {loadingAction === "approve" ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <GitPullRequest className="w-4 h-4" />
            )}
            <span>Approve & create PR</span>
          </button>
        )}

        {/* Gate 2: Reject Action */}
        {actions.includes("reject") && (
          <button
            onClick={handleReject}
            disabled={!!loadingAction}
            className="button"
          >
            {loadingAction === "reject" ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <XCircle className="w-4 h-4" />
            )}
            <span>Reject changes</span>
          </button>
        )}

        {/* Cancel Action */}
        {actions.includes("cancel") && (
          <button
            onClick={handleCancel}
            disabled={!!loadingAction || task.cancelRequested}
            className="button"
          >
            {loadingAction === "cancel" ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <XCircle className="w-4 h-4" />
            )}
            <span>{task.cancelRequested ? "Cancelling..." : "Cancel"}</span>
          </button>
        )}

        {/* Retry PR Action */}
        {actions.includes("retry_pr") && (
          <button
            onClick={() => setApprovalOpen(true)}
            disabled={!!loadingAction || !canApprove}
            className="button primary"
          >
            {loadingAction === "approve" ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            <span>Retry Pull Request</span>
          </button>
        )}

        {/* Re-check Action for External Guidance */}
        {(actions.includes("recheck") || task.status === "resolved_with_guidance") && (
          <button
            onClick={handleRecheck}
            disabled={!!loadingAction}
            className="button"
          >
            {loadingAction === "recheck" ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            <span>I&apos;ve done this, re-check</span>
          </button>
        )}

        {/* Run Again Action */}
        {actions.includes("run_again") && task.status !== "resolved_with_guidance" && (task.status.startsWith("failed_") || task.status === "timed_out" || task.status === "worker_lost") && (
          <button
            onClick={handleRunAgain}
            className="button"
          >
            <RefreshCw className="w-4 h-4" />
            <span>Run again</span>
          </button>
        )}
      </div>
    </div>
  );
}
