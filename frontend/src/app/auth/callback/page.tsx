"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/context/AuthContext";
import { setAuthToken } from "@/lib/api";

export default function GitHubCallback() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const handled = useRef(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (loading || handled.current) return;
    handled.current = true;
    const params = new URLSearchParams(window.location.hash.slice(1));
    const token = params.get("accessToken");
    window.history.replaceState(null, "", window.location.pathname);
    if (!user || !token || params.has("error")) {
      queueMicrotask(() => setError(!user ? "Sign in before connecting GitHub." : "GitHub authorization failed or was cancelled. Please try again."));
      return;
    }
    localStorage.setItem("gitresolve_session_owner", user.uid);
    setAuthToken(token);
    router.replace("/dashboard");
  }, [user, loading, router]);
  return <div className="py-20 text-center space-y-4"><p role={error ? "alert" : "status"}>{error || "Completing GitHub authorization..."}</p>{error && <Link href="/dashboard" className="underline">Return to dashboard</Link>}</div>;
}
