import { TaskStatus } from "@/types";
import { AlertCircle, CheckCircle2, Clock, GitPullRequest, HelpCircle, PauseCircle, PlayCircle, ShieldAlert, XCircle } from "lucide-react";

interface StatusBadgeProps {
  status: TaskStatus;
  showIcon?: boolean;
  size?: "sm" | "md" | "lg";
}

export function StatusBadge({ status, showIcon = true, size = "md" }: StatusBadgeProps) {
  const sizeClasses = {
    sm: "px-2 py-0.5 text-xs font-medium gap-1",
    md: "px-2.5 py-1 text-xs font-semibold gap-1.5",
    lg: "px-3 py-1.5 text-sm font-semibold gap-2",
  }[size];

  switch (status) {
    // Active Working States
    case "queued":
      return (
        <span className={`inline-flex items-center rounded-full bg-slate-800 text-slate-300 border border-slate-700 ${sizeClasses}`}>
          {showIcon && <Clock className="w-3.5 h-3.5 text-slate-400" />}
          Queued
        </span>
      );
    case "planning":
      return (
        <span className={`inline-flex items-center rounded-full bg-indigo-950/80 text-indigo-300 border border-indigo-700/60 ${sizeClasses}`}>
          {showIcon && <span className="w-2 h-2 rounded-full bg-indigo-400 animate-pulse" />}
          Planning Issue
        </span>
      );
    case "running":
      return (
        <span className={`inline-flex items-center rounded-full bg-blue-950/80 text-blue-300 border border-blue-700/60 ${sizeClasses}`}>
          {showIcon && <span className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />}
          Running Repair
        </span>
      );
    case "creating_pr":
      return (
        <span className={`inline-flex items-center rounded-full bg-cyan-950/80 text-cyan-300 border border-cyan-700/60 ${sizeClasses}`}>
          {showIcon && <GitPullRequest className="w-3.5 h-3.5 text-cyan-400 animate-bounce" />}
          Creating PR
        </span>
      );

    // Human Approval Gates
    case "awaiting_start":
      return (
        <span className={`inline-flex items-center rounded-full bg-purple-950 text-purple-200 border border-purple-500 shadow-sm shadow-purple-900/50 ${sizeClasses}`}>
          {showIcon && <PlayCircle className="w-3.5 h-3.5 text-purple-300" />}
          Gate 1: Review Plan
        </span>
      );
    case "awaiting_approval":
      return (
        <span className={`inline-flex items-center rounded-full bg-emerald-950 text-emerald-200 border border-emerald-500 shadow-sm shadow-emerald-900/50 ${sizeClasses}`}>
          {showIcon && <ShieldAlert className="w-3.5 h-3.5 text-emerald-300 animate-pulse" />}
          Gate 2: Review Diff
        </span>
      );

    // Terminal Success
    case "completed":
      return (
        <span className={`inline-flex items-center rounded-full bg-emerald-950/60 text-emerald-300 border border-emerald-800/80 ${sizeClasses}`}>
          {showIcon && <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />}
          Completed (PR Open)
        </span>
      );

    // Terminal Stopped
    case "needs_input":
      return (
        <span className={`inline-flex items-center rounded-full bg-amber-950/70 text-amber-300 border border-amber-800/70 ${sizeClasses}`}>
          {showIcon && <HelpCircle className="w-3.5 h-3.5 text-amber-400" />}
          Needs Input
        </span>
      );
    case "rejected":
      return (
        <span className={`inline-flex items-center rounded-full bg-slate-900 text-slate-400 border border-slate-700 ${sizeClasses}`}>
          {showIcon && <XCircle className="w-3.5 h-3.5 text-slate-500" />}
          Diff Rejected
        </span>
      );
    case "cancelled":
      return (
        <span className={`inline-flex items-center rounded-full bg-slate-900 text-slate-400 border border-slate-700 ${sizeClasses}`}>
          {showIcon && <PauseCircle className="w-3.5 h-3.5 text-slate-500" />}
          Cancelled
        </span>
      );

    // Failure States
    case "failed_not_reproduced":
      return (
        <span className={`inline-flex items-center rounded-full bg-rose-950/70 text-rose-300 border border-rose-800/70 ${sizeClasses}`}>
          {showIcon && <AlertCircle className="w-3.5 h-3.5 text-rose-400" />}
          Not Reproduced
        </span>
      );
    case "failed_no_fix":
      return (
        <span className={`inline-flex items-center rounded-full bg-rose-950/70 text-rose-300 border border-rose-800/70 ${sizeClasses}`}>
          {showIcon && <AlertCircle className="w-3.5 h-3.5 text-rose-400" />}
          No Fix Found
        </span>
      );
    case "failed_policy":
      return (
        <span className={`inline-flex items-center rounded-full bg-rose-950/70 text-rose-300 border border-rose-800/70 ${sizeClasses}`}>
          {showIcon && <ShieldAlert className="w-3.5 h-3.5 text-rose-400" />}
          Policy Violation
        </span>
      );
    case "failed_budget":
      return (
        <span className={`inline-flex items-center rounded-full bg-rose-950/70 text-rose-300 border border-rose-800/70 ${sizeClasses}`}>
          {showIcon && <AlertCircle className="w-3.5 h-3.5 text-rose-400" />}
          Budget Exceeded
        </span>
      );
    case "timed_out":
      return (
        <span className={`inline-flex items-center rounded-full bg-rose-950/70 text-rose-300 border border-rose-800/70 ${sizeClasses}`}>
          {showIcon && <Clock className="w-3.5 h-3.5 text-rose-400" />}
          Timed Out
        </span>
      );
    case "failed_infra":
    case "worker_lost":
      return (
        <span className={`inline-flex items-center rounded-full bg-rose-950/70 text-rose-300 border border-rose-800/70 ${sizeClasses}`}>
          {showIcon && <AlertCircle className="w-3.5 h-3.5 text-rose-400" />}
          Infra Failure
        </span>
      );

    default:
      return (
        <span className={`inline-flex items-center rounded-full bg-slate-800 text-slate-300 border border-slate-700 ${sizeClasses}`}>
          {status}
        </span>
      );
  }
}
