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
        <Dialog.Content className="approval-modal github-auth-modal" aria-describedby="auth-modal-description">
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

              <ol className="auth-setup-steps" aria-label="Connect GitHub in two steps">
                <li><span aria-hidden="true">01</span><div><h3>Choose your repositories</h3><p>Install GitResolve Agent on your account or organization and select the repositories you want to use.</p></div></li>
                <li><span aria-hidden="true">02</span><div><h3>Connect your account</h3><p>Authorize GitHub, then return here to continue to {sectionName}.</p></div></li>
              </ol>
              <p className="auth-setup-hint">Already installed the app? Go straight to Authorize GitHub. <a href="/help#section-1" className="underline underline-offset-4">View setup help ↗</a></p>
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

