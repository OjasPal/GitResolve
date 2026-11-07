"use client";

import { useEffect, useState, useMemo } from "react";
import { RepositoryView } from "@/types";
import {
  Check,
  ChevronDown,
  ExternalLink,
  FolderGit2,
  Globe,
  Lock,
  RefreshCw,
  Search} from "lucide-react";

interface RepositorySelectorProps {
  repositories: RepositoryView[];
  selectedRepoId: string;
  onSelect: (repoId: string) => void;
  loading?: boolean;
  error?: string | null;
  onRefresh?: () => void;
}

export function RepositorySelector({
  repositories,
  selectedRepoId,
  onSelect,
  loading = false,
  error = null,
  onRefresh,
}: RepositorySelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    if (!search.trim()) return repositories;
    const q = search.toLowerCase();
    return repositories.filter(
      (r) =>
        r.fullName.toLowerCase().includes(q) ||
        (r.language && r.language.toLowerCase().includes(q))
    );
  }, [repositories, search]);

  const selectedRepo = repositories.find((r) => r.id === selectedRepoId);

  // Close dropdown on click outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest("[data-repo-selector]")) {
        setIsOpen(false);
      }
    };
    if (isOpen) {
      document.addEventListener("click", handleClickOutside);
      return () => document.removeEventListener("click", handleClickOutside);
    }
  }, [isOpen]);

  return (
    <div className="relative space-y-1.5" data-repo-selector>
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium text-neutral-700 dark:text-neutral-300">
          Target Repository <span className="text-red-500">*</span>
        </label>
        {onRefresh && (
          <button
            type="button"
            onClick={onRefresh}
            disabled={loading}
            className="text-xs text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100 flex items-center gap-1 transition-colors cursor-pointer"
          >
            <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
            <span>Refresh Repositories</span>
          </button>
        )}
      </div>

      {/* Main trigger button */}
      <button
        type="button"
        aria-expanded={isOpen}
        aria-haspopup="dialog"
        disabled={loading}
        onKeyDown={e=>{if(e.key==="Escape")setIsOpen(false)}}
        onClick={() => setIsOpen((prev) => !prev)}
        className="w-full px-3.5 py-2.5 rounded-lg border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-left flex items-center justify-between gap-3 text-sm hover:border-neutral-400 dark:hover:border-neutral-600 transition-colors disabled:opacity-50 cursor-pointer"
      >
        <div className="flex items-center gap-2.5 min-w-0">
          <FolderGit2 className="w-4 h-4 text-neutral-500 shrink-0" />
          {loading ? (
            <span className="text-neutral-400 text-sm">Loading accessible repositories...</span>
          ) : selectedRepo ? (
            <div className="flex items-center gap-2 truncate">
              <span className="font-semibold text-neutral-900 dark:text-neutral-100 truncate">
                {selectedRepo.fullName}
              </span>
              <span className="inline-flex items-center gap-1 text-[11px] font-mono px-1.5 py-0.5 rounded border border-neutral-300 dark:border-neutral-800 text-neutral-500 shrink-0">
                {selectedRepo.private ? (
                  <>
                    <Lock className="w-2.5 h-2.5" />
                    <span>Private</span>
                  </>
                ) : (
                  <>
                    <Globe className="w-2.5 h-2.5" />
                    <span>Public</span>
                  </>
                )}
              </span>
              {selectedRepo.language && (
                <span className="text-xs text-neutral-400 font-mono shrink-0">
                  {selectedRepo.language}
                </span>
              )}
            </div>
          ) : (
            <span className="text-neutral-400">Select a repository...</span>
          )}
        </div>
        <ChevronDown className={`w-4 h-4 text-neutral-400 shrink-0 transition-transform ${isOpen ? "rotate-180" : ""}`} />
      </button>

      {/* Error state */}
      {error && (
        <p className="text-xs text-red-500">{error}</p>
      )}

      {/* Dropdown Popover */}
      {isOpen && (
        <div role="dialog" aria-label="Choose a repository" className="repo-dropdown absolute z-50 left-0 right-0 top-full mt-2 rounded-xl border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 shadow-xl overflow-hidden">
          {/* Search filter input */}
          <div className="p-2 border-b border-neutral-200 dark:border-neutral-800 flex items-center gap-2">
            <Search className="w-4 h-4 text-neutral-400 shrink-0 ml-1" />
            <input
              type="text"
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search repositories"
              onKeyDown={e=>{if(e.key==="Escape")setIsOpen(false)}}
              placeholder="Search Repositories"
              className="w-full text-xs bg-transparent py-1 text-neutral-900 dark:text-neutral-100 placeholder:text-neutral-400 focus:outline-none"
            />
          </div>

          {/* List items */}
          <div className="max-h-60 overflow-y-auto py-1 divide-y divide-neutral-100 dark:divide-neutral-800/50">
            {filtered.length === 0 ? (
              <div className="p-4 text-center text-xs text-neutral-500 space-y-2">
                <p>
                  {repositories.length === 0
                    ? "No repositories found for this GitHub App installation."
                    : "No repositories match your search."}
                </p>
                {repositories.length === 0 && (
                  <a
                    href={process.env.NEXT_PUBLIC_GITHUB_APP_URL || "/repositories"}
                    target={process.env.NEXT_PUBLIC_GITHUB_APP_URL ? "_blank" : undefined}
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-[11px] text-blue-600 dark:text-blue-400 hover:underline"
                  >
                    <span>Configure accessible repositories</span>
                    <ExternalLink className="w-3 h-3" />
                  </a>
                )}
              </div>
            ) : (
              filtered.map((repo) => {
                const isSelected = repo.id === selectedRepoId;
                return (
                  <button
                    key={repo.id}
                    type="button"
                    onClick={() => {
                      onSelect(repo.id);
                      setIsOpen(false);
                      setSearch("");
                    }}
                    className={`w-full px-3 py-2 text-left flex items-center justify-between gap-3 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors cursor-pointer ${
                      isSelected ? "bg-neutral-100/70 dark:bg-neutral-800/70 font-semibold" : ""
                    }`}
                  >
                    <div className="flex items-center gap-2 truncate">
                      <FolderGit2 className="w-3.5 h-3.5 text-neutral-400 shrink-0" />
                      <span className="truncate text-neutral-900 dark:text-neutral-100">
                        {repo.fullName}
                      </span>
                      <span className="text-[10px] font-mono px-1 py-0.5 rounded border border-neutral-200 dark:border-neutral-800 text-neutral-400">
                        {repo.private ? "private" : "public"}
                      </span>
                      {repo.language && (
                        <span className="text-[11px] font-mono text-neutral-400">
                          {repo.language}
                        </span>
                      )}
                    </div>
                    {isSelected && (
                      <Check className="w-4 h-4 text-neutral-900 dark:text-neutral-100 shrink-0" />
                    )}
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
