"use client";

import Link from "next/link";
import { useAuth } from "@/context/AuthContext";
import {
  ArrowRight,
  Cpu,
  FolderGit2,
  GitPullRequest,
  Lock,
  ShieldCheck,
  TestTube2} from "lucide-react";

export default function LandingPage() {
  const { user } = useAuth();

  return (
    <div className="space-y-16 py-8 sm:py-12 max-w-5xl mx-auto">
      {/* Hero Section */}
      <section className="text-center space-y-6 pt-4 sm:pt-8">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-neutral-300 dark:border-neutral-700 bg-neutral-100 dark:bg-neutral-900 text-xs font-mono text-neutral-700 dark:text-neutral-300">
          <ShieldCheck className="w-3.5 h-3.5 text-neutral-500" />
          <span>Human-in-the-Loop Autonomous Engineering</span>
        </div>

        <h1 className="text-3xl sm:text-5xl font-bold tracking-tight text-neutral-900 dark:text-neutral-100 max-w-3xl mx-auto leading-tight">
          Investigate, reproduce, and fix GitHub bugs with AI agent verification.
        </h1>

        <p className="text-base sm:text-lg text-neutral-600 dark:text-neutral-400 max-w-2xl mx-auto leading-relaxed">
          GitResolve accepts GitHub issues, builds structured investigation plans, generates code patches, tests fixes in an isolated Docker execution sandbox, and drafts pull requests for your approval.
        </p>

        {/* Call to action buttons */}
        <div className="flex flex-col sm:flex-row items-center justify-center gap-3 pt-2">
          {user ? (
            <Link
              href="/dashboard"
              className="w-full sm:w-auto px-6 py-3 rounded-lg bg-neutral-900 hover:bg-neutral-800 dark:bg-neutral-100 dark:hover:bg-neutral-200 text-white dark:text-neutral-900 font-semibold text-sm transition-colors flex items-center justify-center gap-2"
            >
              <span>Go to Dashboard</span>
              <ArrowRight className="w-4 h-4" />
            </Link>
          ) : (
            <>
              <Link
                href="/register"
                className="w-full sm:w-auto px-6 py-3 rounded-lg bg-neutral-900 hover:bg-neutral-800 dark:bg-neutral-100 dark:hover:bg-neutral-200 text-white dark:text-neutral-900 font-semibold text-sm transition-colors flex items-center justify-center gap-2"
              >
                <span>Get Started</span>
                <ArrowRight className="w-4 h-4" />
              </Link>
              <Link
                href="/login"
                className="w-full sm:w-auto px-6 py-3 rounded-lg border border-neutral-300 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-800 dark:text-neutral-200 font-medium text-sm transition-colors flex items-center justify-center gap-2"
              >
                <span>Sign In</span>
              </Link>
            </>
          )}
        </div>
      </section>

      {/* Core Workflow Steps */}
      <section className="space-y-6 pt-4">
        <div className="border-b border-neutral-200 dark:border-neutral-800 pb-3">
          <h2 className="text-xl font-bold tracking-tight text-neutral-900 dark:text-neutral-100">
            How GitResolve Operates
          </h2>
          <p className="text-sm text-neutral-500 dark:text-neutral-400 mt-1">
            An end-to-end workflow designed for engineering precision, safety, and transparency.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Step 1 */}
          <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-5 bg-white dark:bg-neutral-900 space-y-2">
            <div className="flex items-center gap-2.5 text-neutral-900 dark:text-neutral-100 font-semibold text-sm">
              <FolderGit2 className="w-4 h-4 text-neutral-600 dark:text-neutral-400" />
              <span>1. GitHub App Repository Authorization</span>
            </div>
            <p className="text-xs text-neutral-600 dark:text-neutral-400 leading-relaxed">
              Connect our dedicated GitHub App to select specific repositories. GitResolve never requests broad personal OAuth scopes or stores repository passwords.
            </p>
          </div>

          {/* Step 2 */}
          <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-5 bg-white dark:bg-neutral-900 space-y-2">
            <div className="flex items-center gap-2.5 text-neutral-900 dark:text-neutral-100 font-semibold text-sm">
              <Cpu className="w-4 h-4 text-neutral-600 dark:text-neutral-400" />
              <span>2. Multi-Model Investigation & Planning</span>
            </div>
            <p className="text-xs text-neutral-600 dark:text-neutral-400 leading-relaxed">
              Our LangGraph agent analyzes the reported issue, inspects relevant files, generates structured hypotheses, and forms an actionable investigation plan before making any code modifications.
            </p>
          </div>

          {/* Step 3 */}
          <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-5 bg-white dark:bg-neutral-900 space-y-2">
            <div className="flex items-center gap-2.5 text-neutral-900 dark:text-neutral-100 font-semibold text-sm">
              <TestTube2 className="w-4 h-4 text-neutral-600 dark:text-neutral-400" />
              <span>3. Isolated Docker Sandbox Execution</span>
            </div>
            <p className="text-xs text-neutral-600 dark:text-neutral-400 leading-relaxed">
              Code patches and repro scripts execute exclusively inside an isolated container with strict resource limits and network isolation. Bounded repair attempts iterate until the test suite passes.
            </p>
          </div>

          {/* Step 4 */}
          <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-5 bg-white dark:bg-neutral-900 space-y-2">
            <div className="flex items-center gap-2.5 text-neutral-900 dark:text-neutral-100 font-semibold text-sm">
              <GitPullRequest className="w-4 h-4 text-neutral-600 dark:text-neutral-400" />
              <span>4. Review-Gated Pull Request Preparation</span>
            </div>
            <p className="text-xs text-neutral-600 dark:text-neutral-400 leading-relaxed">
              Inspect the exact diff hash, test execution outputs, and modified files. A Pull Request is created on GitHub only after explicit human approval. GitResolve never merges code automatically.
            </p>
          </div>
        </div>
      </section>

      {/* Safety & Constraints Notice */}
      <section className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-6 bg-neutral-50 dark:bg-neutral-900/50 space-y-3">
        <h3 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
          <Lock className="w-4 h-4 text-neutral-500" />
          <span>Engineering Policy & Human Oversight Guarantees</span>
        </h3>
        <ul className="text-xs text-neutral-600 dark:text-neutral-400 space-y-1.5 list-disc list-inside">
          <li><strong>No Automatic Merges:</strong> GitResolve opens review PRs; human maintainers retain final approval and merge control.</li>
          <li><strong>Exact-Diff Verification:</strong> The approved diff hash must match the sandbox validation artifact before PR creation can proceed.</li>
          <li><strong>Zero Secret Leakage:</strong> Tokens, environment files, and credentials are wiped and forbidden from artifact logs and commits.</li>
          <li><strong>Isolated Execution:</strong> Untrusted repository code never runs on the host server or within backend worker processes.</li>
        </ul>
      </section>
    </div>
  );
}
