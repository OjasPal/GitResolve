"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  createTask,
  ensureDevAuthenticated,
  listIssues,
  listRepositories,
} from "@/lib/api";
import { IssueListItem, RepositoryView } from "@/types";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  CheckCircle,
  FolderGit2,
  HelpCircle,
  Loader2,
  Play,
  Sparkles,
  Tag,
} from "lucide-react";

export default function NewRepairPage() {
  const router = useRouter();

  const [repositories, setRepositories] = useState<RepositoryView[]>([]);
  const [selectedRepoId, setSelectedRepoId] = useState<string>("");
  const [issues, setIssues] = useState<IssueListItem[]>([]);
  const [selectedIssueNumber, setSelectedIssueNumber] = useState<number | null>(null);

  const [loadingRepos, setLoadingRepos] = useState<boolean>(true);
  const [loadingIssues, setLoadingIssues] = useState<boolean>(false);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // 1. Fetch repositories
  useEffect(() => {
    const init = async () => {
      try {
        setLoadingRepos(true);
        setError(null);
        await ensureDevAuthenticated();
        const res = await listRepositories();
        setRepositories(res.items || []);
        if (res.items && res.items.length > 0) {
          setSelectedRepoId(res.items[0].id);
        }
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Failed to load repositories");
      } finally {
        setLoadingRepos(false);
      }
    };
    init();
  }, []);

  // 2. Fetch issues when repo changes
  useEffect(() => {
    if (!selectedRepoId) return;

    const fetchIssuesForRepo = async () => {
      try {
        setLoadingIssues(true);
        setError(null);
        setSelectedIssueNumber(null);
        const res = await listIssues(selectedRepoId);
        setIssues(res.items || []);
        if (res.items && res.items.length > 0) {
          setSelectedIssueNumber(res.items[0].number);
        }
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Failed to load open issues for repository");
      } finally {
        setLoadingIssues(false);
      }
    };

    fetchIssuesForRepo();
  }, [selectedRepoId]);

  // Selected issue object
  const selectedIssue = issues.find((i) => i.number === selectedIssueNumber);

  // Submit task
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedRepoId || !selectedIssueNumber) {
      setError("Please select both a repository and an open issue.");
      return;
    }

    try {
      setSubmitting(true);
      setError(null);
      const idempotencyKey = crypto.randomUUID();
      const task = await createTask(selectedRepoId, selectedIssueNumber, idempotencyKey);
      router.push(`/tasks/${task.id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to queue repair task.");
      setSubmitting(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Back button */}
      <div>
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200 transition-colors"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>Back to Dashboard</span>
        </Link>
      </div>

      {/* Header */}
      <div className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2.5">
          <Sparkles className="w-6 h-6 text-sky-400" />
          <span>Launch New Automated Repair</span>
        </h1>
        <p className="text-sm text-slate-400">
          Select an accessible GitHub repository and an open issue. The LangGraph agent will investigate the root cause, validate files, and generate a step-by-step fix plan.
        </p>
      </div>

      {/* Error alert */}
      {error && (
        <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-800 text-rose-300 text-sm flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold">Error submitting task</p>
            <p className="text-xs text-rose-300/90">{error}</p>
          </div>
        </div>
      )}

      {/* Main Form Card */}
      <form onSubmit={handleSubmit} className="p-6 rounded-2xl bg-slate-900/60 border border-slate-800 space-y-6 shadow-xl">
        {/* Step 1: Select Repository */}
        <div className="space-y-2">
          <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300">
            1. Select Repository
          </label>
          {loadingRepos ? (
            <div className="flex items-center gap-2 p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-500">
              <Loader2 className="w-4 h-4 animate-spin text-sky-400" />
              <span>Fetching your accessible repositories...</span>
            </div>
          ) : repositories.length === 0 ? (
            <div className="p-4 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-400">
              No repositories found. Ensure you are signed in or have installed the GitHub App on selected repositories.
            </div>
          ) : (
            <select
              value={selectedRepoId}
              onChange={(e) => setSelectedRepoId(e.target.value)}
              className="w-full px-3.5 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500/50 cursor-pointer font-mono"
            >
              {repositories.map((repo) => (
                <option key={repo.id} value={repo.id}>
                  {repo.fullName} ({repo.defaultBranch}) {repo.private ? "🔒 private" : "🌐 public"}
                </option>
              ))}
            </select>
          )}
        </div>

        {/* Step 2: Select Open Issue */}
        <div className="space-y-2">
          <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300">
            2. Choose Existing GitHub Issue
          </label>
          {loadingIssues ? (
            <div className="flex items-center gap-2 p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-500">
              <Loader2 className="w-4 h-4 animate-spin text-sky-400" />
              <span>Loading open issues for selected repository...</span>
            </div>
          ) : issues.length === 0 ? (
            <div className="p-4 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-400">
              No open issues found for this repository.
            </div>
          ) : (
            <select
              value={selectedIssueNumber ?? ""}
              onChange={(e) => setSelectedIssueNumber(Number(e.target.value))}
              className="w-full px-3.5 py-2.5 rounded-xl bg-slate-950 border border-slate-800 text-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500/50 cursor-pointer"
            >
              {issues.map((issue) => (
                <option key={issue.number} value={issue.number}>
                  #{issue.number} — {issue.title}
                </option>
              ))}
            </select>
          )}
        </div>

        {/* Issue Preview Box */}
        {selectedIssue && (
          <div className="p-4 rounded-xl bg-slate-950/80 border border-slate-800/80 space-y-3">
            <div className="flex items-start justify-between gap-4">
              <div>
                <span className="text-[11px] font-mono text-sky-400 font-semibold">
                  Selected Issue #{selectedIssue.number}
                </span>
                <h3 className="text-sm font-bold text-white mt-0.5">
                  {selectedIssue.title}
                </h3>
              </div>
              <a
                href={selectedIssue.url}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-sky-400 hover:underline shrink-0"
              >
                View on GitHub
              </a>
            </div>

            {selectedIssue.labels && selectedIssue.labels.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5">
                {selectedIssue.labels.map((lbl) => (
                  <span
                    key={lbl}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-slate-800 border border-slate-700 text-slate-300 text-[10px]"
                  >
                    <Tag className="w-2.5 h-2.5 text-slate-400" />
                    <span>{lbl}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Safety & Quota Notice */}
        <div className="p-3.5 rounded-xl bg-slate-950/50 border border-slate-800/60 text-xs text-slate-400 space-y-1.5">
          <p className="font-semibold text-slate-300 flex items-center gap-1.5">
            <CheckCircle className="w-3.5 h-3.5 text-emerald-400" />
            <span>Human-in-the-Loop Guarantees</span>
          </p>
          <ul className="list-disc list-inside space-y-0.5 text-slate-400 text-[11px]">
            <li>GitResolve never pushes changes or creates pull requests without your explicit diff approval.</li>
            <li>Repository tests are executed inside an isolated Docker sandbox with zero network access and non-root execution.</li>
            <li>Limited to 1 active concurrent task per user (daily quota: 10 tasks).</li>
          </ul>
        </div>

        {/* Action button */}
        <div className="pt-2 flex items-center justify-end gap-3">
          <Link
            href="/"
            className="px-4 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 font-medium text-xs transition-all cursor-pointer"
          >
            Cancel
          </Link>
          <button
            type="submit"
            disabled={submitting || !selectedRepoId || !selectedIssueNumber}
            className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-sky-500 hover:bg-sky-400 text-slate-950 font-bold text-xs shadow-md shadow-sky-500/20 disabled:opacity-50 transition-all cursor-pointer"
          >
            {submitting ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Queueing Investigation...</span>
              </>
            ) : (
              <>
                <Play className="w-4 h-4 fill-current" />
                <span>Analyze Issue & Create Plan</span>
              </>
            )}
          </button>
        </div>
      </form>
    </div>
  );
}
