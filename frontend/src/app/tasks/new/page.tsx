"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { RepositorySelector } from "@/components/RepositorySelector";
import { createTask, listIssues, listRepositories } from "@/lib/api";
import { IssueListItem, RepositoryView } from "@/types";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Info,
  Loader2,
  Wrench} from "lucide-react";

export default function NewRepairPage() {
  const router = useRouter();

  const [repositories, setRepositories] = useState<RepositoryView[]>([]);
  const [selectedRepoId, setSelectedRepoId] = useState<string>("");
  const [issues, setIssues] = useState<IssueListItem[]>([]);
  const [selectedIssueNumber, setSelectedIssueNumber] = useState<number | null>(null);

  // Keep retries idempotent and ignore stale repository issue responses.
  const requestVersion = useRef(0);
  const submissionKey = useRef<string | null>(null);
  const [issueMode, setIssueMode] = useState<"github" | "manual">("manual");

  const [prompt, setPrompt] = useState("");
  const [issueError, setIssueError] = useState<string | null>(null);

  const [loadingRepos, setLoadingRepos] = useState<boolean>(true);
  const [loadingIssues, setLoadingIssues] = useState<boolean>(false);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // 1. Fetch repositories on load
  const loadRepos = async () => {
    try {
      setLoadingRepos(true);
      setError(null);
      const res = await listRepositories();
      setRepositories(res.items || []);
      if (res.items && res.items.length > 0) {
        const requested = new URLSearchParams(window.location.search).get("repo");
        setSelectedRepoId(res.items.find((r) => r.id === requested)?.id || res.items[0].id);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load accessible repositories");
    } finally {
      setLoadingRepos(false);
    }
  };

  useEffect(() => {
    const timer = setTimeout(() => loadRepos(), 0);
    return () => clearTimeout(timer);
  }, []);

  // 2. Fetch issues whenever repo changes
  useEffect(() => {
    if (!selectedRepoId) return;

    const version = ++requestVersion.current;
    const fetchIssuesForRepo = async () => {
      try {
        setIssues([]);
        setLoadingIssues(true);
        setIssueError(null);
        setSelectedIssueNumber(null);
        const res = await listIssues(selectedRepoId);
        if (version !== requestVersion.current) return;
        setIssues(res.items || []);
        if (res.items && res.items.length > 0) {
          const requested = Number(new URLSearchParams(window.location.search).get("issue"));
          setSelectedIssueNumber(res.items.find((i) => i.number === requested)?.number || res.items[0].number);
          if (requested > 0) setIssueMode("github");
        } else {
          setIssueMode("manual");
        }
      } catch (err: unknown) {
        if (version !== requestVersion.current) return;
        // If repo has no issues or fails
        setIssues([]);
        setIssueError(err instanceof Error ? err.message : "Unable to load issues.");
        setIssueMode("manual");
      } finally {
        if (version === requestVersion.current) setLoadingIssues(false);
      }
    };

    const timer = setTimeout(fetchIssuesForRepo, 0);
    return () => { clearTimeout(timer); requestVersion.current = version + 1; };
  }, [selectedRepoId]);

  // Handle selected issue change in github mode
  const handleIssueSelect = (num: number) => {
    setSelectedIssueNumber(num);

  };

  useEffect(() => { submissionKey.current = null; }, [selectedRepoId, selectedIssueNumber, issueMode, prompt]);

  // Submit task
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedRepoId) {
      setError("Please select a target repository.");
      return;
    }

    if (issueMode === "github" && !selectedIssueNumber) {
      setError("Select an existing issue, or describe the bug instead.");
      return;
    }
    if (issueMode === "manual" && !prompt.trim()) {
      setError("Describe what you want fixed.");
      return;
    }


    try {
      setSubmitting(true);
      setError(null);
      const idempotencyKey = submissionKey.current || crypto.randomUUID();
      submissionKey.current = idempotencyKey;
      const task = await createTask(selectedRepoId, issueMode === "manual" ? prompt.trim() : selectedIssueNumber!, idempotencyKey);
      router.push(`/tasks/${task.id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create repair task.");
      setSubmitting(false);
    }
  };

  return (
    <ProtectedRoute>
      <div className="max-w-3xl mx-auto space-y-6 py-4">
        {/* Breadcrumb / Back button */}
        <div className="flex items-center gap-2 text-xs text-neutral-500">
          <Link
            href="/dashboard"
            className="hover:text-neutral-900 dark:hover:text-neutral-100 flex items-center gap-1 transition-colors"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            <span>Dashboard</span>
          </Link>
          <span>/</span>
          <span className="text-neutral-900 dark:text-neutral-100 font-medium">New Repair</span>
        </div>

        {/* Header */}
        <div className="border-b border-neutral-200 dark:border-neutral-800 pb-4">
          <h1 className="text-2xl font-bold tracking-tight text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
            <Wrench className="w-6 h-6 text-neutral-600 dark:text-neutral-400" />
            <span>Initiate Autonomous Bug Repair</span>
          </h1>
          <p className="text-sm text-neutral-500 dark:text-neutral-400 mt-1">
            Specify a bug for the LangGraph agent to analyze, reproduce in a Docker sandbox, and prepare a fix for review.
          </p>
        </div>

        {/* Error message */}
        {error && (
          <div className="p-4 rounded-xl border border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400 text-xs flex items-start gap-2.5">
            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
            <div className="space-y-1">
              <p className="font-semibold">Unable to initiate task</p>
              <p>{error}</p>
            </div>
          </div>
        )}

        {/* Form Card */}
        <form onSubmit={handleSubmit} className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-6 bg-white dark:bg-neutral-900 shadow-sm space-y-6">
          {/* Field 1: Repository Selector */}
          <RepositorySelector
            repositories={repositories}
            selectedRepoId={selectedRepoId}
            onSelect={(id) => setSelectedRepoId(id)}
            loading={loadingRepos}
            onRefresh={loadRepos}
          />

          {/* Issue Mode Toggle */}
          <div className="space-y-2 pt-2 border-t border-neutral-200 dark:border-neutral-800">
            <div className="flex items-center justify-between">
              <label className="text-xs font-medium text-neutral-700 dark:text-neutral-300">
                What needs fixing?
              </label>
              <div className="flex items-center gap-1 p-0.5 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-100 dark:bg-neutral-800 text-xs">
                <button
                  type="button"
                  onClick={() => setIssueMode("github")}
                  className={`px-2.5 py-1 rounded-md transition-colors cursor-pointer ${
                    issueMode === "github"
                      ? "bg-white dark:bg-neutral-900 font-semibold shadow-xs text-neutral-900 dark:text-neutral-100"
                      : "text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100 disabled:opacity-40"
                  }`}
                >
                  Choose an existing issue
                </button>
                <button
                  type="button"
                  onClick={() => setIssueMode("manual")}
                  className={`px-2.5 py-1 rounded-md transition-colors cursor-pointer ${
                    issueMode === "manual"
                      ? "bg-white dark:bg-neutral-900 font-semibold shadow-xs text-neutral-900 dark:text-neutral-100"
                      : "text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
                  }`}
                >
                  Describe the bug
                </button>
              </div>
            </div>

            {issueMode === "github" && issueError && <p role="alert" className="text-sm text-red-500">{issueError} You can still describe the bug directly.</p>}
            {/* Mode A: Select from Open GitHub issues */}
            {issueMode === "github" && (
              <div className="space-y-2">
                {loadingIssues ? (
                  <div className="p-3 text-xs text-neutral-500 flex items-center gap-2">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>Loading open issues for selected repository...</span>
                  </div>
                ) : issues.length === 0 ? (
                  <p className="text-xs text-neutral-500 italic p-2 border border-neutral-200 dark:border-neutral-800 rounded-lg">
                    No open issues found. Choose “Describe the bug” to start a repair directly.
                  </p>
                ) : (
                  <div className="space-y-1.5 max-h-48 overflow-y-auto border border-neutral-200 dark:border-neutral-800 rounded-lg p-2 divide-y divide-neutral-100 dark:divide-neutral-800">
                    {issues.map((i) => {
                      const isSelected = selectedIssueNumber === i.number;
                      return (
                        <button
                          key={i.number}
                          type="button"
                          onClick={() => handleIssueSelect(i.number)}
                          className={`w-full text-left p-2.5 rounded-md text-xs flex items-start justify-between gap-3 transition-colors cursor-pointer ${
                            isSelected
                              ? "bg-neutral-100 dark:bg-neutral-800 font-semibold text-neutral-900 dark:text-neutral-100"
                              : "hover:bg-neutral-50 dark:hover:bg-neutral-800/50 text-neutral-700 dark:text-neutral-300"
                          }`}
                        >
                          <div className="min-w-0">
                            <span className="font-mono text-neutral-500 mr-2">#{i.number}</span>
                            <span className="truncate">{i.title}</span>
                          </div>
                          {isSelected && <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0 mt-0.5" />}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {issueMode === "manual" && (
              <div className="space-y-2">
                <label htmlFor="bug-description" className="text-sm font-medium">Describe what you want fixed</label>
                <textarea
                  id="bug-description"
                  required
                  maxLength={20000}
                  rows={7}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder="What happens, what should happen, and how can we reproduce it? Include error messages or relevant files if you know them."
                  className="w-full px-3 py-3 text-sm rounded-lg border border-neutral-300 dark:border-neutral-700 bg-transparent focus:outline-none focus:ring-1 focus:ring-neutral-400"
                />
                <p className="text-xs text-neutral-500">No GitHub issue needed. Your description stays with this repair task.</p>
              </div>
            )}
          </div>

          {/* Integration Transparency Note */}
          <div className="p-3.5 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-800/40 text-xs text-neutral-600 dark:text-neutral-400 flex items-start gap-2.5">
            <Info className="w-4 h-4 shrink-0 text-neutral-400 mt-0.5" />
            <p>
              Submitting queues a task in the backend worker. LangGraph first produces a structured plan. You will review the plan and approve starting the Docker execution repair step before code changes are made.
            </p>
          </div>

          {/* Submit Button */}
          <div className="flex items-center justify-end gap-3 pt-2">
            <Link
              href="/dashboard"
              className="px-4 py-2.5 rounded-lg border border-neutral-300 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-700 dark:text-neutral-300 text-sm font-medium transition-colors"
            >
              Cancel
            </Link>
            <button
              type="submit"
              disabled={submitting || loadingRepos || !selectedRepoId || (issueMode === "github" ? loadingIssues || !selectedIssueNumber : !prompt.trim())}
              className="px-6 py-2.5 rounded-lg bg-neutral-900 hover:bg-neutral-800 dark:bg-neutral-100 dark:hover:bg-neutral-200 text-white dark:text-neutral-900 text-sm font-bold transition-colors flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
            >
              {submitting ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Queueing Task...</span>
                </>
              ) : (
                <>
                  <Wrench className="w-4 h-4" />
                  <span>Start Repair</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </ProtectedRoute>
  );
}
