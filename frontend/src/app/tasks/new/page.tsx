"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { RepairComposer } from "@/components/RepairComposer";
import { RepositorySelector } from "@/components/RepositorySelector";
import { createTask, listIssues } from "@/lib/api";
import { IssueListItem } from "@/types";
import { useRepositories } from "@/lib/use-repositories";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Info,
  Loader2,
  Wrench} from "lucide-react";

export default function NewRepairPage() {
  const router = useRouter();

  const repoQuery = useRepositories();
  const { repositories, loading: loadingRepos } = repoQuery;
  const [selectedRepoId, setSelectedRepoId] = useState<string>("");
  const [issues, setIssues] = useState<IssueListItem[]>([]);
  const [nextIssues, setNextIssues] = useState<number | null>(null);
  const [selectedIssueNumber, setSelectedIssueNumber] = useState<number | null>(null);

  // Keep retries idempotent and ignore stale repository issue responses.
  const requestVersion = useRef(0);
  const submissionKey = useRef<string | null>(null);
  const [issueMode, setIssueMode] = useState<"github" | "manual">("manual");

  const [attachmentIds, setAttachmentIds] = useState<string[]>([]);
  const [attachmentsBusy, setAttachmentsBusy] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [issueError, setIssueError] = useState<string | null>(null);

  const [loadingIssues, setLoadingIssues] = useState<boolean>(false);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const loadRepos = () => repoQuery.refresh();
  useEffect(() => {
    if (!repositories.length || selectedRepoId) return;
    const requested = new URLSearchParams(window.location.search).get("repo");
    const timer = setTimeout(() => setSelectedRepoId(repositories.find(r => r.id === requested)?.id || repositories[0].id), 0);
    return () => clearTimeout(timer);
  }, [repositories, selectedRepoId]);

  // 2. Fetch issues whenever repo changes
  useEffect(() => {
    if (!selectedRepoId) return;

    const version = ++requestVersion.current;
    const fetchIssuesForRepo = async () => {
      try {
        setIssues([]);
        setNextIssues(null);
        setLoadingIssues(true);
        setIssueError(null);
        setSelectedIssueNumber(null);
        const res = await listIssues(selectedRepoId);
        if (version !== requestVersion.current) return;
        setIssues(res.items || []);
        setNextIssues(res.nextPage || null);
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

  const loadMoreIssues = async () => {
    if (!nextIssues || loadingIssues) return;
    const version = requestVersion.current;
    setLoadingIssues(true);
    try {
      const res = await listIssues(selectedRepoId, nextIssues);
      if (version !== requestVersion.current) return;
      setIssues(old => [...new Map([...old, ...res.items].map(i => [i.number, i])).values()]);
      setNextIssues(res.nextPage || null);
    } catch (err) { if (version === requestVersion.current) setIssueError(err instanceof Error ? err.message : "Unable to load more issues."); }
    finally { if (version === requestVersion.current) setLoadingIssues(false); }
  };

  useEffect(() => { submissionKey.current = null; }, [selectedRepoId, selectedIssueNumber, issueMode, prompt, attachmentIds]);

  // Submit task
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (attachmentsBusy && issueMode === "manual") return;
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
      const task = await createTask(selectedRepoId, issueMode === "manual" ? prompt.trim() : selectedIssueNumber!, idempotencyKey, issueMode === "manual" ? attachmentIds : []);
      router.push(`/tasks/${task.id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create repair task.");
      setSubmitting(false);
    }
  };

  return (
    <ProtectedRoute>
      <div className="new-repair-page mx-auto space-y-8 py-4">
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
            <span>New repair.</span>
          </h1>
          <p className="text-sm text-neutral-500 dark:text-neutral-400 mt-1">
            Choose a repository. Bring an issue, or describe the bug in your own words.
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
        <div className="new-repair-layout"><form onSubmit={handleSubmit} className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-6 bg-white dark:bg-neutral-900 shadow-sm space-y-6">
          {/* Zero-repository guidance */}
          {!loadingRepos && repositories.length === 0 && (
            <div className="p-3.5 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-950 text-xs flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-neutral-600 dark:text-neutral-400">
                <Info className="w-4 h-4 shrink-0 text-neutral-500" />
                <span>No repositories are currently accessible in your GitHub App installation.</span>
              </div>
              <Link
                href="/repositories"
                className="underline text-neutral-900 dark:text-neutral-100 font-medium shrink-0"
              >
                Manage repositories
              </Link>
            </div>
          )}

          {/* Field 1: Repository Selector */}
          <RepositorySelector
            repositories={repositories}
            selectedRepoId={selectedRepoId}
            onSelect={(id) => setSelectedRepoId(id)}
            loading={loadingRepos}
            onRefresh={loadRepos}
            error={repoQuery.error}
            onLoadMore={repoQuery.hasMore ? () => void repoQuery.loadMore() : undefined}
            loadingMore={repoQuery.loadingMore}
          />

          {/* Issue Mode Toggle */}
          <div className="space-y-2 pt-2 border-t border-neutral-200 dark:border-neutral-800">
            <div className="flex items-center justify-between">

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
              <RepairComposer value={prompt} onChange={setPrompt} onAttachments={setAttachmentIds} onBusyChange={setAttachmentsBusy} disabled={submitting}/>

            )}
            {issueMode === "github" && nextIssues && <button type="button" className="button" disabled={loadingIssues} onClick={()=>void loadMoreIssues()}>Load more issues</button>}
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
              disabled={submitting || loadingRepos || !selectedRepoId || (issueMode === "github" ? loadingIssues || !selectedIssueNumber : !prompt.trim() || attachmentsBusy)}
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
                  <span>Analyze</span>
                </>
              )}
            </button>
          </div>
        </form><aside className="glass-card repair-limits"><span className="eyebrow">You stay in control</span><h2>Plan first.<br/>Repair second.</h2><p className="neutral-note">Review the investigation plan before the agent starts changing code.</p><div className="border-t border-neutral-800 py-5 mt-6"><p className="text-sm">Standard safety limits</p><ul className="text-sm text-neutral-500 space-y-4 mt-5"><li>6 repair attempts</li><li>30 minute task budget</li><li>5 changed files / 150 lines</li><li>Isolated test execution</li></ul></div><p className="neutral-note">The server enforces the limits shown on your created task. A pull request requires your exact-diff approval.</p></aside></div>
      </div>
    </ProtectedRoute>
  );
}
