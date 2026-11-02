"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/context/AuthContext";
import { ApiError, connectGitHub, getAuthToken, getMe, listRepositories } from "@/lib/api";
import { Loader2, FolderGit2 } from "lucide-react";

/** Mount protected screens only once both identity and repository access are ready. */
export function AppAccess({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const { user, loading, signOutUser } = useAuth();
  const [ready, setReady] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const publicPage = ["/", "/login", "/register", "/reset-password", "/auth/callback"].includes(path);
  const appUrl = process.env.NEXT_PUBLIC_GITHUB_APP_URL;

  const verify = useCallback(async () => {
    setReady(false);
    setError(null);
    if (!user) return;
    if (!getAuthToken()) {
      setError("Authorize GitHub to grant access to your installed repositories.");
      return;
    }
    setChecking(true);
    try {
      await getMe();
      const repos = await listRepositories();
      if (!repos.items.length) {
        setError("No authorized repositories found. Install the GitHub App on at least one repository, then authorize GitHub again to refresh access.");
      } else setReady(true);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401
        ? "Your GitHub session expired. Authorize GitHub again to continue."
        : err instanceof Error ? err.message : "Unable to check GitHub access.");
    } finally { setChecking(false); }
  }, [user]);

  useEffect(() => {
    if (!loading && !user && !publicPage) router.replace(`/login?redirect=${encodeURIComponent(path)}`);
    const timer = setTimeout(() => { if (user && !publicPage) void verify(); }, 0);
    const onChange = () => { if (!getAuthToken()) setReady(false); };
    window.addEventListener("auth_change", onChange);
    return () => { clearTimeout(timer); window.removeEventListener("auth_change", onChange); };
  }, [loading, user, publicPage, path, router, verify]);

  if (publicPage) return children;
  if (loading || !user || checking) return <div className="py-24 flex justify-center gap-2" role="status"><Loader2 className="animate-spin w-5 h-5" />Checking account access...</div>;
  if (ready) return children;

  return <section className="max-w-xl mx-auto my-12 p-8 rounded-xl border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 space-y-5">
    <FolderGit2 className="w-8 h-8" />
    <h1 className="text-2xl font-semibold">Connect GitHub to continue</h1>
    <p className="text-sm text-neutral-500">GitHub access is required. Install the GitResolve GitHub App on the repositories you want to repair, then authorize your GitHub account.</p>
    {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
    <div className="flex flex-wrap gap-3">
      {appUrl && <a href={appUrl} target="_blank" rel="noreferrer" className="border rounded-lg px-4 py-2 text-sm">1. Install GitHub App ↗</a>}
      <button onClick={connectGitHub} className="rounded-lg px-4 py-2 text-sm bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900">{appUrl ? "2. Authorize GitHub" : "Connect GitHub"}</button>
      <button onClick={() => signOutUser().catch((err) => setError(err instanceof Error ? err.message : "Unable to sign out."))} className="border rounded-lg px-4 py-2 text-sm">Sign out</button>
      <button onClick={verify} className="border rounded-lg px-4 py-2 text-sm">Check connection</button>
    </div>
    {!appUrl && <p className="text-xs text-neutral-500">The GitHub App installation link has not been configured by the administrator. GitHub authorization must also be enabled on the server.</p>}
  </section>;
}
