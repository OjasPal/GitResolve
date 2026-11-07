"use client";
import { GithubMark } from "./GithubMark";

import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/context/AuthContext";
import { ApiError, connectGitHub, getAuthToken, getMe, listRepositories } from "@/lib/api";
import { Loader2,  ShieldCheck, AlertCircle, ArrowUpRight, Check } from "lucide-react";

/** Mount protected screens only once both identity and repository access are ready. */
export function AppAccess({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const { user, loading, signOutUser } = useAuth();
  const [ready, setReady] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isPublicOrSettings = ["/", "/login", "/register", "/reset-password", "/auth/callback", "/settings"].includes(path);
  const appUrl = process.env.NEXT_PUBLIC_GITHUB_APP_URL;

  const verify = useCallback(async () => {
    setReady(false);
    setError(null);
    if (!user) return;
    if (!getAuthToken()) {
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
    if (!loading && !user && !isPublicOrSettings) router.replace(`/login?redirect=${encodeURIComponent(path)}`);
    const timer = setTimeout(() => { if (user && !isPublicOrSettings) void verify(); }, 0);
    const onChange = () => { if (!getAuthToken()) setReady(false); };
    window.addEventListener("auth_change", onChange);
    return () => { clearTimeout(timer); window.removeEventListener("auth_change", onChange); };
  }, [loading, user, isPublicOrSettings, path, router, verify]);

  if (isPublicOrSettings) return children;
  if (loading || !user || checking) return <div className="py-24 flex justify-center gap-2" role="status"><Loader2 className="animate-spin w-5 h-5" />Checking account access...</div>;
  if (ready) return children;

  return <section className="connection-layout"><div className="connection-stepper" aria-label="GitHub connection progress">{["Account","Install app","Choose repositories","Ready"].map((step,i)=><div key={step} className={i<2?"current":""}><b>{i===0?<Check size={14}/>:i+1}</b><span>{step}</span></div>)}</div><div className="connection-card glass-card facet"><GithubMark size={36}/><span className="eyebrow mt-6">Your code. Your boundaries.</span><h1>Choose what GitResolve can see.</h1><p>Connect GitHub to continue. Install the app on the repositories you want to repair, then authorize your account. You approve every change before it leaves.</p><div className="permission-grid"><div><h3>Reads now</h3><ul><li>Repository code</li><li>Issues</li><li>Repository metadata</li></ul></div><div><h3>Writes only after you approve</h3><ul><li>A proposed branch</li><li>A draft pull request</li><li>No automatic merging</li></ul></div></div>{error&&<div className="inline-error" role="alert"><AlertCircle size={18}/><p>{error}</p></div>}<div className="connection-actions">{appUrl&&<a href={appUrl} target="_blank" rel="noreferrer" className="button primary">Install GitHub App<ArrowUpRight size={16}/></a>}<button onClick={connectGitHub} className={appUrl?"button":"button primary"}>Authorize GitHub<ArrowUpRight size={16}/></button></div><p className="neutral-note flex gap-2 items-center"><ShieldCheck size={14}/>Access is scoped to your GitHub App installation.</p>{!appUrl&&<p className="neutral-note">App installation is currently unavailable. Contact the administrator.</p>}<button className="text-sm underline underline-offset-4" onClick={()=>signOutUser().catch(err=>setError(err instanceof Error?err.message:"Unable to sign out."))}>Sign out</button></div></section>;
}
