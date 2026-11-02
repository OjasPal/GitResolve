"use client";

import { API_BASE_URL } from "@/lib/api";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/context/AuthContext";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { getMe, listRepositories, listTasks } from "@/lib/api";
import { MeResponse, RepositoryView, TaskSummaryItem } from "@/types";
import { StatusBadge } from "@/components/StatusBadge";
import {
  AlertCircle,
  ArrowRight,
  Clock,
  ExternalLink,
  GitPullRequest,
  Inbox,
  Loader2,
  Plus,
  RefreshCw,
  User} from "lucide-react";

export default function DashboardPage() {
  const { user } = useAuth();

  const [profile, setProfile] = useState<MeResponse | null>(null);
  const [repositories, setRepositories] = useState<RepositoryView[]>([]);
  const [tasks, setTasks] = useState<TaskSummaryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadDashboardData = async () => {
    try {
      setLoading(true);
      setError(null);

      // Concurrently fetch profile, repositories, and tasks
      const [profileRes, reposRes, tasksRes] = await Promise.allSettled([
        getMe(),
        listRepositories(),
        listTasks(50),
      ]);

      const failed = [profileRes, reposRes, tasksRes].find((r) => r.status === "rejected");
      if (failed?.status === "rejected") setError(failed.reason instanceof Error ? failed.reason.message : "Unable to load dashboard.");
      if (profileRes.status === "fulfilled") {
        setProfile(profileRes.value);
      }
      if (reposRes.status === "fulfilled") {
        setRepositories(reposRes.value.items || []);
      }
      if (tasksRes.status === "fulfilled") {
        setTasks(tasksRes.value.items || []);
      } else {
        setError(tasksRes.reason instanceof Error ? tasksRes.reason.message : "Failed to load tasks");
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to connect to backend");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const timer = setTimeout(() => loadDashboardData(), 0);
    return () => clearTimeout(timer);
  }, []);

  // Compute metrics from real tasks
  const activeTasks = tasks.filter((t) =>
    ["queued", "planning", "running", "creating_pr"].includes(t.status)
  );
  const awaitingGateTasks = tasks.filter((t) =>
    ["awaiting_start", "awaiting_approval"].includes(t.status)
  );
  const completedTasks = tasks.filter((t) => t.status === "completed" && Boolean(t.pr));
  const failedTasks = tasks.filter(
    (t) =>
      t.status.startsWith("failed_") ||
      t.status === "timed_out" ||
      t.status === "worker_lost" ||
      t.status === "rejected"
  );

  const githubConnected = Boolean(profile?.githubLogin || repositories.length > 0);
  const githubAppUrl =
    process.env.NEXT_PUBLIC_GITHUB_APP_URL || `${API_BASE_URL}/auth/github/login`;

  return (
    <ProtectedRoute>
      <div className="space-y-8 max-w-6xl mx-auto">
        {/* Header bar */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-neutral-200 dark:border-neutral-800 pb-5">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-neutral-900 dark:text-neutral-100">
              Engineering Dashboard
            </h1>
            <p className="text-sm text-neutral-500 dark:text-neutral-400 mt-0.5">
              Overview of connected repositories, active repair jobs, and approval gates
            </p>
          </div>
          <div className="flex items-center gap-2.5">
            <button
              onClick={loadDashboardData}
              disabled={loading}
              className="p-2 rounded-lg border border-neutral-300 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-700 dark:text-neutral-300 transition-colors disabled:opacity-50 cursor-pointer"
              title="Refresh dashboard"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            </button>
            <Link
              href="/tasks/new"
              className="inline-flex items-center gap-2 py-2 px-3.5 rounded-lg bg-neutral-900 hover:bg-neutral-800 dark:bg-neutral-100 dark:hover:bg-neutral-200 text-white dark:text-neutral-900 font-semibold text-xs transition-colors"
            >
              <Plus className="w-3.5 h-3.5 stroke-[2.5]" />
              <span>New Repair</span>
            </Link>
          </div>
        </div>

        {/* Backend / Error Banner */}
        {error && (
          <div className="p-4 rounded-lg border border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400 text-xs flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{error}</span>
            </div>
            <button
              onClick={loadDashboardData}
              className="px-2.5 py-1 rounded bg-red-500/20 hover:bg-red-500/30 text-xs font-medium cursor-pointer"
            >
              Retry
            </button>
          </div>
        )}

        {/* Status / Account overview card */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* Account profile card */}
          <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-4 bg-white dark:bg-neutral-900 space-y-2">
            <span className="text-xs font-mono uppercase text-neutral-500 tracking-wider">Account</span>
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-full bg-neutral-200 dark:bg-neutral-800 flex items-center justify-center text-neutral-700 dark:text-neutral-300 font-bold text-sm shrink-0">
                {profile?.githubLogin ? (
                  profile.githubLogin.slice(0, 2).toUpperCase()
                ) : user?.email ? (
                  user.email.slice(0, 2).toUpperCase()
                ) : (
                  <User className="w-4 h-4" />
                )}
              </div>
              <div className="overflow-hidden">
                <p className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 truncate">
                  {profile?.githubLogin || user?.displayName || user?.email || "Signed In"}
                </p>
                <p className="text-xs text-neutral-500 truncate">
                  {user?.email || `ID: ${profile?.id || user?.uid || "Local"}`}
                </p>
              </div>
            </div>
          </div>

          {/* GitHub Connection card */}
          <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-4 bg-white dark:bg-neutral-900 space-y-2">
            <span className="text-xs font-mono uppercase text-neutral-500 tracking-wider">GitHub App</span>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span
                  className={`w-2.5 h-2.5 rounded-full ${
                    githubConnected ? "bg-emerald-500" : "bg-neutral-400"
                  }`}
                />
                <span className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
                  {githubConnected ? "Connected" : "Not Connected"}
                </span>
              </div>
              {!githubConnected ? (
                <a
                  href={githubAppUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs font-medium text-neutral-900 dark:text-neutral-100 underline flex items-center gap-1"
                >
                  <span>Install App</span>
                  <ExternalLink className="w-3 h-3" />
                </a>
              ) : (
                <Link
                  href="/repositories"
                  className="text-xs font-medium text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
                >
                  Manage
                </Link>
              )}
            </div>
            <p className="text-xs text-neutral-500">
              {repositories.length} {repositories.length === 1 ? "repository" : "repositories"} accessible
            </p>
          </div>

          {/* Connected Repositories summary */}
          <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-4 bg-white dark:bg-neutral-900 space-y-2">
            <span className="text-xs font-mono uppercase text-neutral-500 tracking-wider">Repositories</span>
            <div className="flex items-center justify-between">
              <span className="text-2xl font-bold font-mono text-neutral-900 dark:text-neutral-100">
                {repositories.length}
              </span>
              <Link
                href="/repositories"
                className="text-xs font-medium text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100 flex items-center gap-1"
              >
                <span>View all</span>
                <ArrowRight className="w-3 h-3" />
              </Link>
            </div>
            <p className="text-xs text-neutral-500">
              {repositories.slice(0, 2).map((r) => r.fullName).join(", ") || "No repositories selected"}
            </p>
          </div>
        </div>

        {/* Metrics Grid */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-4 bg-white dark:bg-neutral-900">
            <p className="text-xs font-mono text-neutral-500">Active Tasks</p>
            <p className="text-2xl font-bold font-mono text-neutral-900 dark:text-neutral-100 mt-1">
              {activeTasks.length}
            </p>
          </div>
          <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-4 bg-white dark:bg-neutral-900">
            <p className="text-xs font-mono text-amber-600 dark:text-amber-400">Awaiting Gate</p>
            <p className="text-2xl font-bold font-mono text-amber-600 dark:text-amber-400 mt-1">
              {awaitingGateTasks.length}
            </p>
          </div>
          <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-4 bg-white dark:bg-neutral-900">
            <p className="text-xs font-mono text-emerald-600 dark:text-emerald-400">Completed PRs</p>
            <p className="text-2xl font-bold font-mono text-emerald-600 dark:text-emerald-400 mt-1">
              {completedTasks.length}
            </p>
          </div>
          <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-4 bg-white dark:bg-neutral-900">
            <p className="text-xs font-mono text-neutral-500">Failed / Stopped</p>
            <p className="text-2xl font-bold font-mono text-neutral-900 dark:text-neutral-100 mt-1">
              {failedTasks.length}
            </p>
          </div>
        </div>

        {/* Recent Repair Tasks Section */}
        <div className="space-y-4">
          <div className="flex items-center justify-between border-b border-neutral-200 dark:border-neutral-800 pb-3">
            <h2 className="text-lg font-bold tracking-tight text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
              <Clock className="w-4 h-4 text-neutral-500" />
              <span>Recent Repair Tasks</span>
            </h2>
            <Link
              href="/tasks"
              className="text-xs font-medium text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100 flex items-center gap-1"
            >
              <span>View task history</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>

          {loading ? (
            <div className="py-12 flex flex-col items-center justify-center gap-2 text-neutral-500 text-xs">
              <Loader2 className="w-5 h-5 animate-spin" />
              <span>Retrieving repair tasks from backend...</span>
            </div>
          ) : tasks.length === 0 ? (
            <div className="border border-dashed border-neutral-300 dark:border-neutral-800 rounded-xl p-10 text-center space-y-3">
              <div className="w-10 h-10 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-100 dark:bg-neutral-900 text-neutral-500 flex items-center justify-center mx-auto">
                <Inbox className="w-5 h-5" />
              </div>
              <div className="space-y-1">
                <h3 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
                  No repair tasks found
                </h3>
                <p className="text-xs text-neutral-500 max-w-sm mx-auto">
                  You haven&apos;t started any autonomous repair investigations yet. Select an accessible repository and start your first repair.
                </p>
              </div>
              <div className="pt-2">
                <Link
                  href="/tasks/new"
                  className="inline-flex items-center gap-2 py-2 px-4 rounded-lg bg-neutral-900 hover:bg-neutral-800 dark:bg-neutral-100 dark:hover:bg-neutral-200 text-white dark:text-neutral-900 text-xs font-semibold"
                >
                  <Plus className="w-3.5 h-3.5 stroke-[2.5]" />
                  <span>Create First Repair Task</span>
                </Link>
              </div>
            </div>
          ) : (
            <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl overflow-hidden bg-white dark:bg-neutral-900">
              <div className="divide-y divide-neutral-200 dark:divide-neutral-800">
                {tasks.slice(0, 10).map((t) => (
                  <Link
                    key={t.id}
                    href={`/tasks/${t.id}`}
                    className="flex flex-col sm:flex-row sm:items-center justify-between p-4 hover:bg-neutral-50 dark:hover:bg-neutral-800/50 transition-colors gap-3"
                  >
                    <div className="space-y-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs text-neutral-500">
                          {t.repoFullName} {t.issue.number ? `#${t.issue.number}` : "Direct report"}
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
                      <span className="text-xs text-neutral-400">
                        {new Date(t.createdAt!).toLocaleDateString()}
                      </span>
                      <ArrowRight className="w-4 h-4 text-neutral-400" />
                    </div>
                  </Link>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </ProtectedRoute>
  );
}
