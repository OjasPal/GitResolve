"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ensureDevAuthenticated, listIssues, listRepositories } from "@/lib/api";
import { IssueListItem, RepositoryView } from "@/types";
import {
  AlertCircle,
  ExternalLink,
  FolderGit2,
  GitFork,
  GitPullRequest,
  Globe,
  Lock,
  PlusCircle,
  RefreshCw,
  Tag,
  Wrench,
} from "lucide-react";

export default function RepositoriesPage() {
  const [repositories, setRepositories] = useState<RepositoryView[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [selectedRepo, setSelectedRepo] = useState<string | null>(null);
  const [issues, setIssues] = useState<IssueListItem[]>([]);
  const [loadingIssues, setLoadingIssues] = useState<boolean>(false);

  const loadRepos = async () => {
    try {
      setLoading(true);
      await ensureDevAuthenticated();
      const res = await listRepositories();
      setRepositories(res.items || []);
      if (res.items && res.items.length > 0) {
        setSelectedRepo(res.items[0].id);
      }
    } catch {
      // error handled
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadRepos();
  }, []);

  useEffect(() => {
    if (!selectedRepo) return;
    const fetchIssues = async () => {
      try {
        setLoadingIssues(true);
        const res = await listIssues(selectedRepo);
        setIssues(res.items || []);
      } catch {
        setIssues([]);
      } finally {
        setLoadingIssues(false);
      }
    };
    fetchIssues();
  }, [selectedRepo]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2.5">
            <FolderGit2 className="w-6 h-6 text-sky-400" />
            <span>Accessible Repositories</span>
          </h1>
          <p className="text-sm text-slate-400 mt-0.5">
            Repositories with GitResolve GitHub App access ready for automated repair workflows
          </p>
        </div>

        <button
          onClick={loadRepos}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-900 border border-slate-800 text-slate-300 hover:text-white text-xs font-semibold cursor-pointer shrink-0"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          <span>Refresh Repositories</span>
        </button>
      </div>

      {/* Grid of Repositories */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {repositories.map((repo) => {
          const isSelected = selectedRepo === repo.id;

          return (
            <div
              key={repo.id}
              onClick={() => setSelectedRepo(repo.id)}
              className={`p-5 rounded-2xl border transition-all cursor-pointer ${
                isSelected
                  ? "bg-slate-900/90 border-sky-500 shadow-md shadow-sky-500/10 ring-1 ring-sky-500/30"
                  : "bg-slate-900/50 border-slate-800 hover:border-slate-700"
              }`}
            >
              <div className="flex items-start justify-between gap-3 mb-3">
                <div className="flex items-center gap-2.5">
                  <div className="w-8 h-8 rounded-lg bg-slate-800 flex items-center justify-center text-sky-400 font-bold text-xs">
                    <FolderGit2 className="w-4 h-4" />
                  </div>
                  <div>
                    <h3 className="font-bold text-white text-sm tracking-tight">
                      {repo.fullName}
                    </h3>
                    <div className="flex items-center gap-2 text-[11px] text-slate-400 font-mono mt-0.5">
                      <span>branch: {repo.defaultBranch}</span>
                      <span>·</span>
                      <span>{repo.language || "Python"}</span>
                    </div>
                  </div>
                </div>

                <span
                  className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold border ${
                    repo.private
                      ? "bg-slate-800 border-slate-700 text-slate-400"
                      : "bg-emerald-950/40 border-emerald-800/60 text-emerald-300"
                  }`}
                >
                  {repo.private ? <Lock className="w-2.5 h-2.5" /> : <Globe className="w-2.5 h-2.5" />}
                  <span>{repo.private ? "Private" : "Public"}</span>
                </span>
              </div>

              <div className="pt-2 border-t border-slate-800/80 flex items-center justify-between text-xs">
                <span className="text-slate-400 text-[11px]">
                  {isSelected ? "Currently viewing open issues" : "Click to view open issues"}
                </span>
                <Link
                  href="/tasks/new"
                  onClick={(e) => e.stopPropagation()}
                  className="flex items-center gap-1 text-sky-400 hover:text-sky-300 font-semibold text-xs"
                >
                  <Wrench className="w-3.5 h-3.5" />
                  <span>Start Repair</span>
                </Link>
              </div>
            </div>
          );
        })}
      </div>

      {/* Selected Repo Open Issues */}
      {selectedRepo && (
        <div className="rounded-2xl border border-slate-800 bg-slate-900/50 p-6 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold text-white uppercase tracking-wider">
              Open Issues for Selected Repository
            </h2>
            <span className="text-xs text-slate-400 font-mono">
              {issues.length} open issue{issues.length !== 1 ? "s" : ""}
            </span>
          </div>

          {loadingIssues ? (
            <p className="text-xs text-slate-500">Loading issues...</p>
          ) : issues.length === 0 ? (
            <p className="text-xs text-slate-500">No open issues found for this repository.</p>
          ) : (
            <div className="divide-y divide-slate-800/60">
              {issues.map((issue) => (
                <div key={issue.number} className="py-3 flex items-center justify-between gap-4">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sky-400 font-bold text-xs">#{issue.number}</span>
                      <span className="text-sm font-medium text-slate-200">{issue.title}</span>
                    </div>
                    {issue.labels && issue.labels.length > 0 && (
                      <div className="flex items-center gap-1.5">
                        {issue.labels.map((l) => (
                          <span
                            key={l}
                            className="text-[10px] px-2 py-0.5 rounded-full bg-slate-800 text-slate-400 border border-slate-700"
                          >
                            {l}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>

                  <Link
                    href="/tasks/new"
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-sky-500 hover:bg-sky-400 text-slate-950 font-bold text-xs shrink-0 cursor-pointer"
                  >
                    <Wrench className="w-3.5 h-3.5" />
                    <span>Launch Fix</span>
                  </Link>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
