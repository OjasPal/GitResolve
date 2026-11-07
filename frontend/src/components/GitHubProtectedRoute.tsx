"use client";

import React, { useEffect, useRef } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuth } from "@/context/AuthContext";
import { useGitHubAuth } from "@/context/GitHubAuthContext";
import { Loader2 } from "lucide-react";

export function GitHubProtectedRoute({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { user, loading: authLoading } = useAuth();
  const { isAuthorized, loading: ghLoading, openAuthModal } = useGitHubAuth();
  const redirectedRef = useRef(false);

  const isChecking = authLoading || ghLoading;

  useEffect(() => {
    // Wait until both Firebase auth and GitHub auth status checks have completed
    if (isChecking) return;

    // If user is not logged into Firebase at all, base ProtectedRoute redirects to login
    if (!user) return;

    // If logged into Firebase but NOT authorized with GitHub:
    if (!isAuthorized && !redirectedRef.current) {
      redirectedRef.current = true;
      // 1. Redirect to dashboard with preserved destination query parameter
      router.replace(`/dashboard?auth_required=true&redirect=${encodeURIComponent(pathname)}`);
      // 2. Open the authorization popup modal
      openAuthModal({ returnUrl: pathname });
    }
  }, [user, isAuthorized, isChecking, pathname, router, openAuthModal]);

  // While checking authorization status, do NOT render protected content even briefly
  if (isChecking) {
    return (
      <div className="py-24 flex flex-col items-center justify-center gap-3" role="status">
        <Loader2 className="animate-spin w-6 h-6 text-neutral-400" />
        <p className="text-sm text-neutral-500 font-mono">Verifying GitHub authorization...</p>
      </div>
    );
  }

  // If not authorized, withhold protected page content completely
  if (!isAuthorized) {
    return null;
  }

  // Once authorization is verified, render the protected content
  return <>{children}</>;
}

