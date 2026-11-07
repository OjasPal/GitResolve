"use client";

import React from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useGitHubAuth } from "@/context/GitHubAuthContext";
import { connectGitHub } from "@/lib/api";
import { GithubMark } from "./GithubMark";
import { ArrowUpRight, ShieldCheck, X, AlertCircle } from "lucide-react";

export function GitHubAuthModal() {
  const {
    isAuthModalOpen,
    closeAuthModal,
    pendingDestination,
    error,
  } = useGitHubAuth();

  const appUrl = process.env.NEXT_PUBLIC_GITHUB_APP_URL;

  // Format destination into a user-friendly section name
  const getSectionName = (path: string | null): string => {
    if (!path) return "this section";
    if (path.startsWith("/tasks/new")) return "New Repair";
    if (path.startsWith("/tasks")) return "Repair Tasks";
    if (path.startsWith("/repositories")) return "Repositories";
    return "this section";
  };

  const sectionName = getSectionName(pendingDestination);

  return (
    <Dialog.Root open={isAuthModalOpen} onOpenChange={(open) => { if (!open) closeAuthModal(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-scrim" />
        <Dialog.Content className="approval-modal" aria-describedby="auth-modal-description">
          <div className="flex items-start justify-between gap-4 mb-4">
            <div className="flex items-center gap-3">
              <GithubMark size={32} />
              <span className="eyebrow">GitHub Authorization</span>
            </div>
            <Dialog.Close asChild>
              <button
                className="icon-button"
                aria-label="Close dialog"
                onClick={closeAuthModal}
              >
                <X size={18} />
              </button>
            </Dialog.Close>
          </div>

          <Dialog.Title asChild>
            <h2 className="text-xl font-medium tracking-tight mb-2">
              Connect Your GitHub Account
            </h2>
          </Dialog.Title>

          <Dialog.Description asChild>
            <div id="auth-modal-description" className="space-y-4 text-sm text-[var(--text-2)] leading-relaxed">
              <p>
                GitHub authorization is required to access {sectionName}. Please install the GitHub App and authorize your account to continue.
              </p>

              {error && (
                <div className="inline-error my-2 p-3 text-xs" role="alert">
                  <AlertCircle size={16} className="shrink-0" />
                  <p>{error}</p>
                </div>
              )}

              <div className="permission-grid py-4 my-3 text-xs border-y border-[var(--stroke)]">
                <div>
                  <h3 className="font-mono text-xs uppercase tracking-wider mb-2 text-[var(--text-3)]">
                    Reads Now
                  </h3>
                  <ul className="space-y-1 text-[var(--text-2)]">
                    <li>• Repository code</li>
                    <li>• GitHub issues</li>
                    <li>• Repository metadata</li>
                  </ul>
                </div>
                <div>
                  <h3 className="font-mono text-xs uppercase tracking-wider mb-2 text-[var(--text-3)]">
                    Writes (Upon Approval)
                  </h3>
                  <ul className="space-y-1 text-[var(--text-2)]">
                    <li>• Proposed repair branch</li>
                    <li>• Draft pull request</li>
                    <li>• Never auto-merges</li>
                  </ul>
                </div>
              </div>

              {appUrl && (
                <div className="p-3 bg-[var(--glass)] border border-[var(--stroke)] rounded text-xs text-[var(--text-2)] space-y-1">
                  <p className="font-medium text-[var(--text)]">First time setup?</p>
                  <p>Install the GitHub App on the repositories you want GitResolve to analyze, then authorize your account.</p>
                </div>
              )}
            </div>
          </Dialog.Description>

          <div className="actions mt-6 flex flex-wrap items-center justify-end gap-3 pt-4 border-t border-[var(--stroke)]">
            <button
              type="button"
              className="button"
              onClick={closeAuthModal}
            >
              Cancel
            </button>

            {appUrl && (
              <a
                href={appUrl}
                target="_blank"
                rel="noreferrer"
                className="button"
              >
                Install GitHub App <ArrowUpRight size={14} />
              </a>
            )}

            <button
              type="button"
              className="button primary"
              onClick={() => {
                connectGitHub();
              }}
            >
              Authorize GitHub <ArrowUpRight size={14} />
            </button>
          </div>

          <p className="neutral-note mt-4 text-xs flex items-center gap-1.5 text-[var(--text-3)]">
            <ShieldCheck size={14} />
            Access is securely scoped. You retain final approval on all code changes.
          </p>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

