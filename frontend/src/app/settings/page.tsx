"use client";

import { API_BASE_URL } from "@/lib/api";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/context/AuthContext";
import { useTheme } from "@/context/ThemeContext";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import {
  checkBackendHealth,
  getMe,
  listRepositories} from "@/lib/api";
import { MeResponse } from "@/types";
import {
  AlertCircle,
  ExternalLink,
  FolderGit2,
  LogOut,
  Moon,
  RefreshCw,
  Server,
  Settings,
  Sun,
  User} from "lucide-react";

export default function SettingsPage() {
  const router = useRouter();
  const { user, signOutUser } = useAuth();
  const { theme, toggleTheme } = useTheme();

  const [profile, setProfile] = useState<MeResponse | null>(null);
  const [repoCount, setRepoCount] = useState<number>(0);
  const [backendHealth, setBackendHealth] = useState<{ status: string }>({ status: "checking" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sync = async () => {
    try {
      setLoading(true);
      setError(null);
      const [healthRes, meRes, reposRes] = await Promise.allSettled([
        checkBackendHealth(),
        getMe(),
        listRepositories(),
      ]);

      if (healthRes.status === "fulfilled") {
        setBackendHealth(healthRes.value);
      }
      const failed = [meRes, reposRes].find((r) => r.status === "rejected");
      if (failed?.status === "rejected") setError(failed.reason instanceof Error ? failed.reason.message : "Unable to refresh account.");
      if (meRes.status === "fulfilled") {
        setProfile(meRes.value);
      }
      if (reposRes.status === "fulfilled") {
        setRepoCount(reposRes.value.items?.length || 0);
      }
    } catch {
      setError("Unable to refresh account settings.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const timer = setTimeout(() => sync(), 0);
    return () => clearTimeout(timer);
  }, []);

  const handleSignOut = async () => {
    await signOutUser();
    router.push("/login");
  };

  const githubAppUrl =
    process.env.NEXT_PUBLIC_GITHUB_APP_URL || `${API_BASE_URL}/auth/github/login`;

  return (
    <ProtectedRoute>
      <div className="space-y-8 max-w-4xl mx-auto py-2">
        {/* Header */}
        <div className="border-b border-neutral-200 dark:border-neutral-800 pb-4">
          <h1 className="text-2xl font-bold tracking-tight text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
            <Settings className="w-6 h-6 text-neutral-600 dark:text-neutral-400" />
            <span>Settings & Preferences</span>
          </h1>
          <p className="text-sm text-neutral-500 dark:text-neutral-400 mt-0.5">
            Manage your account identity, GitHub App connection, and workspace preferences
          </p>
        </div>

        {/* Message / Error banners */}
        {error && (
          <div className="p-3.5 rounded-lg border border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400 text-xs flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Section 1: Account Profile */}
        <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-6 bg-white dark:bg-neutral-900 shadow-sm space-y-4">
          <div className="flex items-center justify-between border-b border-neutral-100 dark:border-neutral-800 pb-3">
            <h2 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
              <User className="w-4 h-4 text-neutral-500" />
              <span>User Profile</span>
            </h2>
            <button
              onClick={handleSignOut}
              className="text-xs font-medium text-red-600 dark:text-red-400 hover:underline flex items-center gap-1.5 cursor-pointer"
            >
              <LogOut className="w-3.5 h-3.5" />
              <span>Sign Out</span>
            </button>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
            <div className="p-3 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-950 space-y-1">
              <span className="text-neutral-500 font-mono">Display Name</span>
              <p className="font-semibold text-neutral-900 dark:text-neutral-100">
                {user?.displayName || profile?.githubLogin || "Developer"}
              </p>
            </div>

            <div className="p-3 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-950 space-y-1">
              <span className="text-neutral-500 font-mono">Email Address</span>
              <p className="font-semibold text-neutral-900 dark:text-neutral-100">
                {user?.email || "No email associated"}
              </p>
            </div>

            <div className="p-3 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-950 space-y-1">
              <span className="text-neutral-500 font-mono">Account Provider</span>
              <p className="font-semibold text-neutral-900 dark:text-neutral-100 uppercase font-mono">
                {user?.providerId || "Local / Session"}
              </p>
            </div>

            <div className="p-3 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-950 space-y-1">
              <span className="text-neutral-500 font-mono">User ID</span>
              <p className="font-mono text-neutral-900 dark:text-neutral-100 truncate">
                {user?.uid || profile?.id || "N/A"}
              </p>
            </div>
          </div>
        </div>

        {/* Section 2: GitHub App Installation */}
        <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-6 bg-white dark:bg-neutral-900 shadow-sm space-y-4">
          <div className="border-b border-neutral-100 dark:border-neutral-800 pb-3">
            <h2 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
              <FolderGit2 className="w-4 h-4 text-neutral-500" />
              <span>GitHub App Integration</span>
            </h2>
            <p className="text-xs text-neutral-500 mt-0.5">
              Repository access is granted exclusively through our GitHub App installation on your GitHub account or organization
            </p>
          </div>

          <div className="p-4 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-950 space-y-3">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="flex items-center gap-2.5">
                <span
                  className={`w-3 h-3 rounded-full ${
                    profile?.githubLogin || repoCount > 0 ? "bg-emerald-500" : "bg-neutral-400"
                  }`}
                />
                <div>
                  <p className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
                    {profile?.githubLogin ? `Connected as @${profile.githubLogin}` : repoCount > 0 ? "GitHub App Connected" : "Not Connected"}
                  </p>
                  <p className="text-xs text-neutral-500">
                    {repoCount} authorized repositor{repoCount === 1 ? "y" : "ies"} accessible to GitResolve
                  </p>
                </div>
              </div>

              <a
                href={githubAppUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg bg-neutral-900 hover:bg-neutral-800 dark:bg-neutral-100 dark:hover:bg-neutral-200 text-white dark:text-neutral-900 text-xs font-semibold transition-colors shrink-0"
              >
                <span>Manage in GitHub</span>
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
            </div>

            <p className="text-xs text-neutral-500 leading-relaxed border-t border-neutral-200 dark:border-neutral-800 pt-3">
              To add or revoke repositories, visit your GitHub account settings &gt; Applications &gt; GitResolve. Changes made on GitHub take effect immediately.
            </p>
          </div>
        </div>

        {/* Section 3: Preferences (Light/Dark Theme) */}
        <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-6 bg-white dark:bg-neutral-900 shadow-sm space-y-4">
          <div className="border-b border-neutral-100 dark:border-neutral-800 pb-3">
            <h2 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
              <Sun className="w-4 h-4 text-neutral-500" />
              <span>Appearance & Preferences</span>
            </h2>
          </div>

          <div className="flex items-center justify-between p-3 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-950">
            <div className="space-y-0.5">
              <p className="text-xs font-semibold text-neutral-900 dark:text-neutral-100">
                Interface Theme
              </p>
              <p className="text-xs text-neutral-500">
                Current mode: <strong className="capitalize">{theme}</strong>
              </p>
            </div>

            <button
              type="button"
              onClick={toggleTheme}
              className="px-3 py-1.5 rounded-lg border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 hover:bg-neutral-100 dark:hover:bg-neutral-800 text-xs font-medium text-neutral-800 dark:text-neutral-200 flex items-center gap-2 transition-colors cursor-pointer"
            >
              {theme === "dark" ? (
                <>
                  <Sun className="w-3.5 h-3.5" />
                  <span>Switch to Light</span>
                </>
              ) : (
                <>
                  <Moon className="w-3.5 h-3.5" />
                  <span>Switch to Dark</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Section 4: Backend Infrastructure Health */}
        <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-6 bg-white dark:bg-neutral-900 shadow-sm space-y-4">
          <div className="border-b border-neutral-100 dark:border-neutral-800 pb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
              <Server className="w-4 h-4 text-neutral-500" />
              <span>Backend & Agent Infrastructure</span>
            </h2>
            <button
              onClick={sync}
              className="text-xs text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100 flex items-center gap-1 cursor-pointer"
            >
              <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
              <span>Re-check</span>
            </button>
          </div>

          <div className="p-3.5 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-950 text-xs flex items-center justify-between font-mono">
            <span className="text-neutral-500">FastAPI Server Health:</span>
            <span
              className={`font-semibold ${
                backendHealth.status === "ok" || backendHealth.status === "healthy"
                  ? "text-emerald-600 dark:text-emerald-400"
                  : "text-amber-600 dark:text-amber-400"
              }`}
            >
              {backendHealth.status.toUpperCase()}
            </span>
          </div>
        </div>


      </div>
    </ProtectedRoute>
  );
}
