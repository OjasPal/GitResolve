"use client";
import { GithubMark } from "./GithubMark";

import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/context/AuthContext";
import { ApiError, connectGitHub, getAuthToken, getMe } from "@/lib/api";
import { ShieldCheck, AlertCircle, ArrowUpRight, Check } from "lucide-react";

/** Identity is checked in the background; repository fetches belong to their screens. */
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
    setError(null);
    if (!user) return;
    if (!getAuthToken()) {
      return;
    }
    setChecking(true);
    setReady(true);
    try {
      await getMe();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401
        ? "Your GitHub session expired. Authorize GitHub again to continue."
        : err instanceof Error ? err.message : "Unable to check GitHub access.");
    } finally { setChecking(false); }
  }, [user]);

  useEffect(() => {
    if (!loading && !user && !publicPage) router.replace(`/login?redirect=${encodeURIComponent(path)}`);
  }, [loading, user, publicPage, path, router]);

  useEffect(() => {
    const timer = setTimeout(() => { if (user && !publicPage) void verify(); }, 0);
    const onChange = () => { setReady(false); if (getAuthToken()) void verify(); };
    window.addEventListener("auth_change", onChange);
    return () => { clearTimeout(timer); window.removeEventListener("auth_change", onChange); };
  }, [loading, user, publicPage, verify]);

  if (publicPage) return children;
  if (loading || !user) return <div className="glass-card min-h-64 animate-pulse" role="status">Loading your workspace…</div>;
  if (ready) return <div key={user.uid}>{checking && <span className="sr-only" role="status">Checking account access…</span>}{error && <div className="inline-error" role="alert"><AlertCircle size={18}/><p>{error}</p><button onClick={()=>void verify()}>Retry</button></div>}{children}</div>;

  return <section className="connection-layout"><div className="connection-stepper" aria-label="GitHub connection progress">{["Account","Install app","Choose repositories","Ready"].map((step,i)=><div key={step} className={i<2?"current":""}><b>{i===0?<Check size={14}/>:i+1}</b><span>{step}</span></div>)}</div><div className="connection-card glass-card facet"><GithubMark size={36}/><span className="eyebrow mt-6">Your code. Your boundaries.</span><h1>Choose what GitResolve can see.</h1><p>Connect GitHub to continue. Install the app on the repositories you want to repair, then authorize your account. You approve every change before it leaves.</p><div className="permission-grid"><div><h3>Reads now</h3><ul><li>Repository code</li><li>Issues</li><li>Repository metadata</li></ul></div><div><h3>Writes only after you approve</h3><ul><li>A proposed branch</li><li>A draft pull request</li><li>No automatic merging</li></ul></div></div>{error&&<div className="inline-error" role="alert"><AlertCircle size={18}/><p>{error}</p></div>}<div className="connection-actions">{appUrl&&<a href={appUrl} target="_blank" rel="noreferrer" className="button primary">Install GitHub App<ArrowUpRight size={16}/></a>}<button onClick={connectGitHub} className={appUrl?"button":"button primary"}>Authorize GitHub<ArrowUpRight size={16}/></button></div><p className="neutral-note flex gap-2 items-center"><ShieldCheck size={14}/>Access is scoped to your GitHub App installation.</p>{!appUrl&&<p className="neutral-note">App installation is currently unavailable. Contact the administrator.</p>}<button className="text-sm underline underline-offset-4" onClick={()=>signOutUser().catch(err=>setError(err instanceof Error?err.message:"Unable to sign out."))}>Sign out</button></div></section>;
}
