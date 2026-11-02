"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { listTasks } from "@/lib/api";
import { TaskSummaryItem } from "@/types";
import { StatusBadge } from "@/components/StatusBadge";
import {
  AlertCircle,
  ArrowRight,
  GitPullRequest,
  Inbox,
  ListTodo,
  Loader2,
  Plus,
  RefreshCw,
  Search} from "lucide-react";

export default function TasksPage() {
  const [tasks, setTasks] = useState<TaskSummaryItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [filterStatus, setFilterStatus] = useState<string>("all");

  const loadTasks = async (append = false, next: string | null = null) => {
    try {
      setLoading(true);
      setError(null);
      const res = await listTasks(50, append ? next : null);
      setTasks((prev) => append ? [...prev, ...res.items.filter((item) => !prev.some((t) => t.id === item.id))] : res.items);
      setCursor(res.nextCursor || null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load tasks");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const timer = setTimeout(() => loadTasks(), 0);
    return () => clearTimeout(timer);
  }, []);

  const filteredTasks = tasks.filter((t) => {
    const repo = t.repoFullName || "";
    const matchesSearch =
      repo.toLowerCase().includes(search.toLowerCase()) ||
      String(t.issue.number).includes(search) ||
      (t.issue.title && t.issue.title.toLowerCase().includes(search.toLowerCase())) ||
      t.id.toLowerCase().includes(search.toLowerCase());

    const matchesStatus =
      filterStatus === "all" ||
      (filterStatus === "active" && ["queued", "planning", "running", "creating_pr"].includes(t.status)) ||
      (filterStatus === "review" && ["awaiting_start", "awaiting_approval"].includes(t.status)) ||
      (filterStatus === "completed" && t.status === "completed") ||
      (filterStatus === "failed" && (t.status.startsWith("failed_") || t.status === "timed_out" || t.status === "worker_lost"));

    return matchesSearch && matchesStatus;
  });

  return (
    <ProtectedRoute>
      <div className="space-y-6 max-w-6xl mx-auto py-2">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-neutral-200 dark:border-neutral-800 pb-4">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
              <ListTodo className="w-6 h-6 text-neutral-600 dark:text-neutral-400" />
              <span>Repair Tasks</span>
            </h1>
            <p className="text-sm text-neutral-500 dark:text-neutral-400 mt-0.5">
              History of autonomous bug investigations, test runs, and pull requests
            </p>
          </div>

          <div className="flex items-center gap-2.5">
            <button
              onClick={() => loadTasks()}
              disabled={loading}
              className="p-2 rounded-lg border border-neutral-300 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-700 dark:text-neutral-300 text-xs font-semibold cursor-pointer disabled:opacity-50"
              title="Refresh tasks"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            </button>
            <Link
              href="/tasks/new"
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-neutral-900 hover:bg-neutral-800 dark:bg-neutral-100 dark:hover:bg-neutral-200 text-white dark:text-neutral-900 text-xs font-semibold"
            >
              <Plus className="w-3.5 h-3.5 stroke-[2.5]" />
              <span>New Repair</span>
            </Link>
          </div>
        </div>

        {/* Error message */}
        {error && (
          <div className="p-3.5 rounded-lg border border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400 text-xs flex items-center justify-between">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{error}</span>
            </div>
            <button onClick={() => loadTasks()} className="underline text-xs cursor-pointer">
              Retry
            </button>
          </div>
        )}

        {/* Search & Filter Controls */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
          <div className="relative flex-1">
            <Search className="w-4 h-4 text-neutral-400 absolute left-3.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by repository, issue #, title, or task ID..."
              className="w-full pl-9 pr-4 py-2 text-xs rounded-lg border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 placeholder:text-neutral-400 focus:outline-none focus:ring-1 focus:ring-neutral-400"
            />
          </div>

          <div className="flex items-center gap-1.5 p-1 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-100 dark:bg-neutral-900 text-xs overflow-x-auto">
            {["all", "active", "review", "completed", "failed"].map((cat) => (
              <button
                key={cat}
                type="button"
                onClick={() => setFilterStatus(cat)}
                className={`px-3 py-1 rounded-md capitalize transition-colors cursor-pointer whitespace-nowrap ${
                  filterStatus === cat
                    ? "bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 font-semibold shadow-xs"
                    : "text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
                }`}
              >
                {cat}
              </button>
            ))}
          </div>
        </div>

        {/* Tasks List */}
        {loading ? (
          <div className="py-20 flex flex-col items-center justify-center gap-2 text-neutral-500 text-xs font-mono">
            <Loader2 className="w-5 h-5 animate-spin" />
            <span>Loading tasks from backend...</span>
          </div>
        ) : filteredTasks.length === 0 ? (
          <div className="border border-dashed border-neutral-300 dark:border-neutral-800 rounded-xl p-12 text-center space-y-3">
            <Inbox className="w-8 h-8 text-neutral-400 mx-auto" />
            <div className="space-y-1">
              <h3 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
                No matching tasks found
              </h3>
              <p className="text-xs text-neutral-500">
                {tasks.length === 0
                  ? "No tasks have been queued yet."
                  : "Try clearing your search query or filter category."}
              </p>
            </div>
            {tasks.length === 0 && (
              <div className="pt-2">
                <Link
                  href="/tasks/new"
                  className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 text-xs font-semibold"
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>Start a New Repair</span>
                </Link>
              </div>
            )}
          </div>
        ) : (
          <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl overflow-hidden bg-white dark:bg-neutral-900 shadow-sm divide-y divide-neutral-200 dark:divide-neutral-800">
            {filteredTasks.map((t) => {
              const repo = t.repoFullName || "";
              return (
                <Link
                  key={t.id}
                  href={`/tasks/${t.id}`}
                  className="flex flex-col sm:flex-row sm:items-center justify-between p-4 hover:bg-neutral-50 dark:hover:bg-neutral-800/50 transition-colors gap-3"
                >
                  <div className="space-y-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs text-neutral-500">
                        {repo} {t.issue.number ? `#${t.issue.number}` : "Direct report"}
                      </span>
                      <StatusBadge status={t.status} />
                    </div>
                    <p className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 truncate">
                      {t.issue.title || `Issue #${t.issue.number}`}
                    </p>
                    <div className="flex items-center gap-3 text-xs text-neutral-500 font-mono">
                      <span>Stage: {t.stage}</span>
                      <span>Attempts: {t.attempts.used}/{t.attempts.max}</span>
                      {t.pr && (
                        <span className="text-emerald-600 dark:text-emerald-400 flex items-center gap-1">
                          <GitPullRequest className="w-3 h-3" />
                          <span>PR #{t.pr.number}</span>
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="shrink-0 flex items-center gap-2 self-end sm:self-center">
                    <span className="text-xs text-neutral-400 font-mono">
                      {new Date(t.createdAt!).toLocaleDateString()}
                    </span>
                    <ArrowRight className="w-4 h-4 text-neutral-400" />
                  </div>
                </Link>
              );
            })}
          </div>
        )}
        {cursor && <button disabled={loading} onClick={() => loadTasks(true, cursor)} className="border rounded-lg px-4 py-2 text-sm disabled:opacity-50">Load more tasks</button>}
      </div>
    </ProtectedRoute>
  );
}
