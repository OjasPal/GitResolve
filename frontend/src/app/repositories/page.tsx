"use client";

import { API_BASE_URL } from "@/lib/api";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { listIssues } from "@/lib/api";
import { useRepositories } from "@/lib/use-repositories";
import { IssueListItem } from "@/types";
import {
  AlertCircle,
  ExternalLink,
  FolderGit2,
  GitBranch,
  RefreshCw,
  Search,
  Wrench} from "lucide-react";

export default function RepositoriesPage() {
  const repoQuery = useRepositories();
  const { repositories, loading } = repoQuery;
  const [issueError, setError] = useState<string | null>(null);
  const error = repoQuery.error || issueError;
  const [selectedRepo, setSelectedRepo] = useState<string | null>(null);
  const [issues, setIssues] = useState<IssueListItem[]>([]);
  const [loadingIssues, setLoadingIssues] = useState<boolean>(false);
  const [search, setSearch] = useState("");
  const [nextIssues, setNextIssues] = useState<number | null>(null);
  const issueVersion = useRef(0);

  const loadRepos = () => repoQuery.refresh();

  useEffect(() => {
    if (selectedRepo || !repositories.length) return;
    const timer = setTimeout(() => setSelectedRepo(repositories[0].id), 0);
    return () => clearTimeout(timer);
  }, [repositories, selectedRepo]);

  useEffect(() => {
    if (!selectedRepo) return;
    const version = ++issueVersion.current;
    let cancelled = false;
    const fetchIssues = async () => {
      try {
        setLoadingIssues(true);
        setIssues([]);
        setNextIssues(null);
        setError(null);
        const res = await listIssues(selectedRepo);
        if (!cancelled) { setIssues(res.items || []); setNextIssues(res.nextPage || null); }
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Unable to load issues.");
        setIssues([]);
      } finally {
        if (!cancelled) setLoadingIssues(false);
      }
    };
    const timer = setTimeout(fetchIssues, 0);
    return () => { cancelled = true; issueVersion.current = version + 1; clearTimeout(timer); };
  }, [selectedRepo]);

  const loadMoreIssues = async () => {
    if (!selectedRepo || !nextIssues || loadingIssues) return;
    const version = issueVersion.current;
    setLoadingIssues(true);
    try {
      const result = await listIssues(selectedRepo, nextIssues);
      if (version !== issueVersion.current) return;
      setIssues(old => [...new Map([...old, ...result.items].map(i => [i.number, i])).values()]);
      setNextIssues(result.nextPage || null);
    } catch (err) { if (version === issueVersion.current) setError(err instanceof Error ? err.message : "Unable to load more issues."); }
    finally { if (version === issueVersion.current) setLoadingIssues(false); }
  };

  const filteredRepos = repositories.filter((r) =>
    r.fullName.toLowerCase().includes(search.toLowerCase()) ||
    (r.language && r.language.toLowerCase().includes(search.toLowerCase()))
  );

  const activeRepo = repositories.find((r) => r.id === selectedRepo);
  const githubAppUrl =
    process.env.NEXT_PUBLIC_GITHUB_APP_URL || `${API_BASE_URL}/auth/github/login`;

  return (
    <ProtectedRoute>
      <div className="space-y-6 max-w-6xl mx-auto py-2">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-neutral-200 dark:border-neutral-800 pb-4">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
              <FolderGit2 className="w-6 h-6 text-neutral-600 dark:text-neutral-400" />
              <span>Repositories.</span>
            </h1>
            <p className="text-sm text-neutral-500 dark:text-neutral-400 mt-0.5">
              Missing a shared repository? Ask its owner or organization admin to enable GitResolve Agent on it, then refresh your GitHub authorization.
            </p>
          </div>

          <div className="flex items-center gap-2.5">
            <button
              type="button"
              onClick={() => window.location.reload()}
              aria-label="Refresh repositories"
              className="p-2 rounded-lg border border-neutral-300 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-700 dark:text-neutral-300 text-xs font-semibold cursor-pointer disabled:opacity-50"
              title="Refresh repositories"
            >
              <RefreshCw className={`w-4 h-4 ${loading || repoQuery.refreshing ? "animate-spin" : ""}`} />
            </button>
            <a
              href={githubAppUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-neutral-300 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-800 dark:text-neutral-200 text-xs font-medium transition-colors"
            >
              <span>Add or remove repositories</span>
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          </div>
        </div>

        {/* Error notice */}
        {error && (
          <div className="p-3.5 rounded-lg border border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400 text-xs flex items-center justify-between">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{error}</span>
            </div>
            <button onClick={loadRepos} className="underline text-xs cursor-pointer">
              Retry
            </button>
          </div>
        )}

        {/* Search */}
        <div className="relative">
          <Search className="w-4 h-4 text-neutral-400 absolute left-3.5 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search authorized repositories" placeholder="Search your repositories…"
            className="w-full pl-9 pr-4 py-2 text-xs rounded-lg border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-neutral-900 dark:text-neutral-100 placeholder:text-neutral-400 focus:outline-none focus:ring-1 focus:ring-neutral-400"
          />
        </div>

        {/* 2-Column Layout: Repositories on left, Issue details on right */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Left Column: Repo list */}
          <div className="md:col-span-1 border border-neutral-200 dark:border-neutral-800 rounded-xl bg-white dark:bg-neutral-900 overflow-hidden divide-y divide-neutral-200 dark:divide-neutral-800">
            <div className="p-3 bg-neutral-50 dark:bg-neutral-950 font-mono text-xs text-neutral-500 flex items-center justify-between">
              <span>Installed Repositories</span>
              <span>{repositories.length} loaded</span>
            </div>

            {loading ? (
              <div className="p-8 text-center text-xs text-neutral-400 font-mono">
                Loading repositories...
              </div>
            ) : filteredRepos.length === 0 ? (
              <div className="p-6 text-center text-xs text-neutral-500 space-y-2">
                <p>
                  {repositories.length === 0
                    ? "No repositories are currently accessible."
                    : "No repositories match your search."}
                </p>
                <a
                  href={githubAppUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-block text-neutral-900 dark:text-neutral-100 underline text-xs"
                >
                  {repositories.length === 0
                    ? "Configure repositories in GitHub App"
                    : "Install GitHub App on an account"}
                </a>
              </div>
            ) : (
              <div className="max-h-[500px] overflow-y-auto divide-y divide-neutral-100 dark:divide-neutral-800/50">
                {filteredRepos.map((repo) => {
                  const isSelected = repo.id === selectedRepo;
                  return (
                    <button
                      key={repo.id}
                      type="button"
                      onClick={() => setSelectedRepo(repo.id)}
                      className={`w-full p-3.5 text-left transition-colors cursor-pointer space-y-1 ${
                        isSelected
                          ? "bg-neutral-100 dark:bg-neutral-800/80 font-medium"
                          : "hover:bg-neutral-50 dark:hover:bg-neutral-800/30"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-semibold text-neutral-900 dark:text-neutral-100 truncate">
                          {repo.fullName}
                        </span>
                        <span className="text-[10px] font-mono px-1 py-0.5 rounded border border-neutral-200 dark:border-neutral-800 text-neutral-500 shrink-0">
                          {repo.private ? "private" : "public"}
                        </span>
                      </div>
                      <div className="flex items-center gap-3 text-[11px] font-mono text-neutral-400">
                        <span className="flex items-center gap-1">
                          <GitBranch className="w-3 h-3" />
                          <span>{repo.defaultBranch}</span>
                        </span>
                        {repo.language && <span>{repo.language}</span>}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
            {repoQuery.hasMore && <button className="button w-full" disabled={repoQuery.loadingMore} onClick={()=>void repoQuery.loadMore()}>Load more repositories</button>}
          </div>

          {/* Right Column: Selected Repo issues & action */}
          <div className="md:col-span-2 border border-neutral-200 dark:border-neutral-800 rounded-xl bg-white dark:bg-neutral-900 p-5 space-y-5">
            {activeRepo ? (
              <>
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-neutral-100 dark:border-neutral-800 pb-4">
                  <div className="space-y-1">
                    <h2 className="text-lg font-bold text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
                      <span>{activeRepo.fullName}</span>
                      <span className="text-xs font-mono px-2 py-0.5 rounded border border-neutral-200 dark:border-neutral-800 text-neutral-500 font-normal">
                        {activeRepo.private ? "Private" : "Public"}
                      </span>
                    </h2>
                    <p className="text-xs font-mono text-neutral-500">
                      Default branch: {activeRepo.defaultBranch}
                    </p>
                  </div>

                  <Link
                    href={`/tasks/new?repo=${encodeURIComponent(activeRepo.id)}`}
                    className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-neutral-900 hover:bg-neutral-800 dark:bg-neutral-100 dark:hover:bg-neutral-200 text-white dark:text-neutral-900 text-xs font-semibold self-start sm:self-center"
                  >
                    <Wrench className="w-3.5 h-3.5" />
                    <span>Fix an issue</span>
                  </Link>
                </div>

                {nextIssues && <button className="button" disabled={loadingIssues} onClick={()=>void loadMoreIssues()}>Load more issues</button>}
                {/* Open issues list */}
                <div className="space-y-3">
                  <h3 className="text-xs font-mono uppercase tracking-wider text-neutral-500">
                    Open GitHub Issues ({issues.length})
                  </h3>

                  {loadingIssues ? (
                    <div className="p-8 text-center text-xs text-neutral-400 font-mono">
                      Querying issues from repository...
                    </div>
                  ) : issues.length === 0 ? (
                    <div className="p-8 text-center text-xs text-neutral-500 border border-dashed border-neutral-200 dark:border-neutral-800 rounded-lg">
                      No open issues found in this repository.
                    </div>
                  ) : (
                    <div className="space-y-2 max-h-96 overflow-y-auto divide-y divide-neutral-100 dark:divide-neutral-800 border border-neutral-200 dark:border-neutral-800 rounded-lg p-2">
                      {issues.map((i) => (
                        <div
                          key={i.number}
                          className="p-3 flex items-start justify-between gap-3 text-xs"
                        >
                          <div className="space-y-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="font-mono text-neutral-500">#{i.number}</span>
                              <span className="font-semibold text-neutral-900 dark:text-neutral-100">
                                {i.title}
                              </span>
                            </div>
                            {i.labels && i.labels.length > 0 && (
                              <div className="flex flex-wrap gap-1 pt-0.5">
                                {i.labels.map((lbl, idx) => (
                                  <span
                                    key={idx}
                                    className="px-1.5 py-0.5 rounded text-[10px] font-mono border border-neutral-200 dark:border-neutral-800 text-neutral-500"
                                  >
                                    {lbl}
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>

                          <Link
                            href={`/tasks/new?repo=${encodeURIComponent(activeRepo.id)}&issue=${i.number}`}
                            className="shrink-0 px-2.5 py-1 rounded border border-neutral-300 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 text-xs font-medium text-neutral-800 dark:text-neutral-200 transition-colors"
                          >
                            Fix Issue
                          </Link>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div className="p-12 text-center text-xs text-neutral-500">
                {repositories.length === 0
                  ? "No repositories are currently accessible. Configure your repository selection in GitHub App settings."
                  : "Select a repository to view details and open issues."}
              </div>
            )}
          </div>
        </div>
      </div>
    </ProtectedRoute>
  );
}
