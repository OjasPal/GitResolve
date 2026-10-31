"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { TaskResponse } from "@/types";
import { approveTask, cancelTask, rejectTask, startTask } from "@/lib/api";
import { AlertTriangle, CheckCircle, GitPullRequest, Loader2, Play, RefreshCw, XCircle } from "lucide-react";

interface ActionButtonsProps {
  task: TaskResponse;
  reviewedDiffHash: string | null;
  onActionSuccess: (updatedTask: TaskResponse) => void;
}

export function ActionButtons({ task, reviewedDiffHash, onActionSuccess }: ActionButtonsProps) {
  const router = useRouter();
  const [loadingAction, setLoadingAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const actions = task.availableActions || [];

  const handleStart = async () => {
    try {
      setLoadingAction("start");
      setError(null);
      const updated = await startTask(task.id);
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
      const updated = await approveTask(task.id, task.finalDiffHash);
      onActionSuccess(updated);
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
    router.push("/tasks/new");
  };

  if (actions.length === 0) {
    if (task.status === "creating_pr") {
      return (
        <div className="flex items-center gap-2 text-cyan-400 text-sm font-medium">
          <Loader2 className="w-4 h-4 animate-spin" />
          <span>Opening Pull Request on GitHub...</span>
        </div>
      );
    }
    return null;
  }

  return (
    <div className="flex flex-col gap-2">
      {error && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-rose-950/80 border border-rose-800 text-rose-300 text-xs">
          <AlertTriangle className="w-4 h-4 shrink-0 text-rose-400" />
          <span>{error}</span>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2.5">
        {/* Gate 1: Start Repair Action */}
        {actions.includes("start") && (
          <button
            onClick={handleStart}
            disabled={!!loadingAction}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-sm transition-all shadow-md shadow-emerald-900/40 disabled:opacity-50 cursor-pointer"
          >
            {loadingAction === "start" ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Play className="w-4 h-4 fill-current" />
            )}
            <span>Start Repair (Gate 1)</span>
          </button>
        )}

        {/* Gate 2: Approve & Create PR Action */}
        {actions.includes("approve") && (
          <button
            onClick={handleApprove}
            disabled={!!loadingAction || reviewedDiffHash !== task.finalDiffHash}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-sm transition-all shadow-md shadow-emerald-900/40 disabled:opacity-50 cursor-pointer"
          >
            {loadingAction === "approve" ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <GitPullRequest className="w-4 h-4" />
            )}
            <span>Approve & Create PR (Gate 2)</span>
          </button>
        )}

        {/* Gate 2: Reject Action */}
        {actions.includes("reject") && (
          <button
            onClick={handleReject}
            disabled={!!loadingAction}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-slate-800 hover:bg-rose-950/60 border border-slate-700 hover:border-rose-800 text-slate-300 hover:text-rose-300 font-medium text-sm transition-all disabled:opacity-50 cursor-pointer"
          >
            {loadingAction === "reject" ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <XCircle className="w-4 h-4" />
            )}
            <span>Reject Diff</span>
          </button>
        )}

        {/* Cancel Action */}
        {actions.includes("cancel") && (
          <button
            onClick={handleCancel}
            disabled={!!loadingAction || task.cancelRequested}
            className="flex items-center gap-2 px-3.5 py-2 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-400 hover:text-amber-300 font-medium text-sm transition-all disabled:opacity-50 cursor-pointer"
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
            onClick={handleApprove}
            disabled={!!loadingAction || reviewedDiffHash !== task.finalDiffHash}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-white font-medium text-sm transition-all disabled:opacity-50 cursor-pointer"
          >
            {loadingAction === "approve" ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            <span>Retry Pull Request</span>
          </button>
        )}

        {/* Run Again Action */}
        {actions.includes("run_again") && (
          <button
            onClick={handleRunAgain}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 font-medium text-sm transition-all cursor-pointer"
          >
            <RefreshCw className="w-4 h-4" />
            <span>New Repair Run</span>
          </button>
        )}
      </div>
    </div>
  );
}
