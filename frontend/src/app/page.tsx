"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ensureDevAuthenticated, listTasks } from "@/lib/api";
import { TaskSummaryItem } from "@/types";
import { StatusBadge } from "@/components/StatusBadge";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock,
  ExternalLink,
  GitPullRequest,
  Inbox,
  Layers,
  Loader2,
  PlusCircle,
  RefreshCw,
  ShieldAlert,
  Sparkles,
} from "lucide-react";

export default function DashboardPage() {
  const [tasks, setTasks] = useState<TaskSummaryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchTasks = async () => {
    try {
      await ensureDevAuthenticated();
      const res = await listTasks(50);
      setTasks(res.items || []);
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to connect to GitResolve backend."
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let active = true;
    ensureDevAuthenticated().then(() => listTasks(50)).then((res) => {
      if (active) { setTasks(res.items || []);  }
    }).catch((err: unknown) => {
      if (active) { setError(err instanceof Error ? err.message : "Unable to load tasks."); }
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  // Compute metrics
  const total = tasks.length;
  const active = tasks.filter((t) =>
    ["queued", "planning", "running", "creating_pr"].includes(t.status)
  ).length;
  const awaitingGate = tasks.filter((t) =>
    ["awaiting_start", "awaiting_approval"].includes(t.status)
  ).length;
  const completed = tasks.filter((t) => t.status === "completed").length;
  const failed = tasks.filter(
    (t) =>
      t.status.startsWith("failed_") ||
      t.status === "timed_out" ||
      t.status === "worker_lost"
  ).length;

  return (
    <div className="space-y-8 animate-fadeIn">
      {/* Hero / Quick Action Banner */}
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-r from-slate-900 via-slate-900 to-indigo-950/70 border border-slate-800 p-6 sm:p-8 shadow-xl">
        <div className="relative z-10 flex flex-col md:flex-row md:items-center justify-between gap-6">
          <div className="space-y-2 max-w-2xl">
            <div className="inline-flex items-center gap-2 px-2.5 py-1 rounded-full bg-sky-500/10 border border-sky-500/30 text-sky-400 text-xs font-semibold">
              <Sparkles className="w-3.5 h-3.5" />
              <span>Autonomous Repair Engine Online</span>
            </div>
            <h1 className="text-2xl sm:text-3xl font-bold tracking-tight text-white">
              Investigate & Resolve GitHub Issues Automatically
            </h1>
            <p className="text-sm text-slate-400 leading-relaxed">
              GitResolve combines Gemini-backed LangGraph planning with Docker sandbox test execution. Inspect bugs, generate reproductions, and open verified Pull Requests with full human approval gates.
            </p>
          </div>

          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 shrink-0">
            <Link
              href="/tasks/new"
              className="flex items-center justify-center gap-2 px-5 py-3 rounded-xl bg-sky-500 hover:bg-sky-400 text-slate-950 font-bold text-sm transition-all shadow-lg shadow-sky-500/30 hover:shadow-sky-500/50 cursor-pointer"
            >
              <PlusCircle className="w-4 h-4 stroke-[2.5]" />
              <span>New Repair Task</span>
            </Link>
            <button
              onClick={() => { setLoading(true); setError(null); void fetchTasks(); }}
              className="flex items-center justify-center gap-2 px-4 py-3 rounded-xl bg-slate-800/80 hover:bg-slate-700/80 border border-slate-700 text-slate-200 font-medium text-sm transition-all cursor-pointer"
              title="Refresh task list"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
              <span className="hidden sm:inline">Refresh</span>
            </button>
          </div>
        </div>
      </div>

      {/* Metrics Row */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        {/* Total Tasks */}
        <div className="p-4 sm:p-5 rounded-xl bg-slate-900/60 border border-slate-800">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-slate-400">Total Tasks</span>
            <Layers className="w-4 h-4 text-slate-500" />
          </div>
          <div className="text-2xl sm:text-3xl font-extrabold text-white">{total}</div>
          <span className="text-[11px] text-slate-500">Lifetime runs</span>
        </div>

        {/* Active Working */}
        <div className="p-4 sm:p-5 rounded-xl bg-slate-900/60 border border-slate-800">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-slate-400">Active Working</span>
            <Activity className="w-4 h-4 text-sky-400 animate-pulse" />
          </div>
          <div className="text-2xl sm:text-3xl font-extrabold text-sky-400">{active}</div>
          <span className="text-[11px] text-slate-500">In worker loop</span>
        </div>

        {/* Needs Human Gate Attention */}
        <div className="p-4 sm:p-5 rounded-xl bg-purple-950/20 border border-purple-900/40">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-purple-300">Needs Review</span>
            <ShieldAlert className="w-4 h-4 text-purple-400 animate-bounce" />
          </div>
          <div className="text-2xl sm:text-3xl font-extrabold text-purple-300">{awaitingGate}</div>
          <span className="text-[11px] text-purple-400/80">Gate 1 or Gate 2</span>
        </div>

        {/* Completed PRs */}
        <div className="p-4 sm:p-5 rounded-xl bg-slate-900/60 border border-slate-800">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-slate-400">Completed PRs</span>
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="text-2xl sm:text-3xl font-extrabold text-emerald-400">{completed}</div>
          <span className="text-[11px] text-slate-500">Merged / Ready PR</span>
        </div>

        {/* Failed / Blocked */}
        <div className="p-4 sm:p-5 rounded-xl bg-slate-900/60 border border-slate-800 col-span-2 lg:col-span-1">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-slate-400">Failed / Blocked</span>
            <AlertTriangle className="w-4 h-4 text-rose-400" />
          </div>
          <div className="text-2xl sm:text-3xl font-extrabold text-rose-400">{failed}</div>
          <span className="text-[11px] text-slate-500">Unreproduced / timeout</span>
        </div>
      </div>

      {/* Human Gate Banner if attention needed */}
      {awaitingGate > 0 && (
        <div className="flex items-center justify-between p-4 rounded-xl bg-gradient-to-r from-purple-950/60 to-indigo-950/60 border border-purple-700/60 shadow-lg shadow-purple-950/40">
          <div className="flex items-center gap-3">
            <ShieldAlert className="w-5 h-5 text-purple-400 shrink-0" />
            <div>
              <p className="text-sm font-semibold text-purple-200">
                {awaitingGate} task{awaitingGate > 1 ? "s" : ""} awaiting your explicit human review!
              </p>
              <p className="text-xs text-purple-300/80">
                Review the AI investigation plan or inspect proposed code changes before PR creation.
              </p>
            </div>
          </div>
          <Link
            href="/tasks"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-500 text-white font-medium text-xs transition-all shrink-0"
          >
            <span>Review Tasks</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </Link>
        </div>
      )}

      {/* Error state */}
      {error && (
        <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-800 text-rose-300 text-sm flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0" />
            <span>{error}</span>
          </div>
          <button
            onClick={() => { setLoading(true); setError(null); void fetchTasks(); }}
            className="text-xs underline hover:text-white"
          >
            Retry Connection
          </button>
        </div>
      )}

      {/* Recent Tasks Table */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/50 overflow-hidden shadow-lg">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800">
          <div>
            <h2 className="text-base font-bold text-white">Recent Repair Tasks</h2>
            <p className="text-xs text-slate-400">
              Live monitoring of automated issue investigations and test runs
            </p>
          </div>
          <Link
            href="/tasks"
            className="text-xs text-sky-400 hover:text-sky-300 font-medium flex items-center gap-1"
          >
            <span>View All</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </Link>
        </div>

        {loading ? (
          <div className="flex flex-col items-center justify-center p-12 text-slate-500">
            <Loader2 className="w-7 h-7 text-sky-400 animate-spin mb-3" />
            <p className="text-sm">Fetching tasks from backend...</p>
          </div>
        ) : tasks.length === 0 ? (
          <div className="flex flex-col items-center justify-center p-12 text-slate-500 text-center">
            <Inbox className="w-10 h-10 mb-3 text-slate-600" />
            <h3 className="text-sm font-semibold text-slate-300 mb-1">No repair tasks yet</h3>
            <p className="text-xs text-slate-500 max-w-sm mb-4">
              Select a repository and an open GitHub issue to launch your first automated investigation.
            </p>
            <Link
              href="/tasks/new"
              className="px-4 py-2 rounded-lg bg-sky-500 hover:bg-sky-400 text-slate-950 font-semibold text-xs transition-all shadow-md shadow-sky-500/20"
            >
              Start New Repair
            </Link>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-950/60 text-slate-400 uppercase text-[10px] tracking-wider border-b border-slate-800">
                <tr>
                  <th className="py-3 px-6">Task ID</th>
                  <th className="py-3 px-6">Repository</th>
                  <th className="py-3 px-6">Issue</th>
                  <th className="py-3 px-6">Status</th>
                  <th className="py-3 px-6">Attempts</th>
                  <th className="py-3 px-6">Created</th>
                  <th className="py-3 px-6 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {tasks.map((task) => {
                  const createdAt = new Date(task.createdAt).toLocaleString([], {
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  });

                  return (
                    <tr
                      key={task.id}
                      className="hover:bg-slate-800/40 transition-colors group"
                    >
                      <td className="py-3.5 px-6 font-mono text-slate-300 font-medium">
                        <Link
                          href={`/tasks/${task.id}`}
                          className="text-sky-400 hover:underline flex items-center gap-1.5"
                        >
                          <span>{task.id.slice(0, 14)}...</span>
                        </Link>
                      </td>
                      <td className="py-3.5 px-6 font-mono text-slate-300">
                        {task.repoFullName}
                      </td>
                      <td className="py-3.5 px-6">
                        <div className="font-medium text-slate-200 line-clamp-1 max-w-xs">
                          #{task.issue.number} {task.issue.title || "Untitled Issue"}
                        </div>
                      </td>
                      <td className="py-3.5 px-6">
                        <StatusBadge status={task.status} size="sm" />
                      </td>
                      <td className="py-3.5 px-6 font-mono text-slate-400">
                        {task.attempts.used} / {task.attempts.max}
                      </td>
                      <td className="py-3.5 px-6 text-slate-400 whitespace-nowrap">
                        {createdAt}
                      </td>
                      <td className="py-3.5 px-6 text-right whitespace-nowrap">
                        <Link
                          href={`/tasks/${task.id}`}
                          className="inline-flex items-center gap-1 text-sky-400 hover:text-sky-300 font-semibold"
                        >
                          <span>View Details</span>
                          <ArrowRight className="w-3 h-3 group-hover:translate-x-0.5 transition-transform" />
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
