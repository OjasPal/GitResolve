"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { useAuth } from "./AuthContext";
import {
  ApiError,
  clearAuthToken,
  getAuthToken,
  getMe,
  listRepositories,
} from "@/lib/api";
import { MeResponse, RepositoryView } from "@/types";

export interface GitHubAuthContextType {
  isAuthorized: boolean;
  hasInstalledApp: boolean;
  loading: boolean;
  error: string | null;
  profile: MeResponse | null;
  repositories: RepositoryView[];
  checkAuth: () => Promise<boolean>;
  openAuthModal: (options?: { returnUrl?: string; message?: string }) => void;
  closeAuthModal: () => void;
  isAuthModalOpen: boolean;
  pendingDestination: string | null;
}

const GitHubAuthContext = createContext<GitHubAuthContextType>({
  isAuthorized: false,
  hasInstalledApp: false,
  loading: true,
  error: null,
  profile: null,
  repositories: [],
  checkAuth: async () => false,
  openAuthModal: () => {},
  closeAuthModal: () => {},
  isAuthModalOpen: false,
  pendingDestination: null,
});

export function isProtectedPath(pathname: string): boolean {
  if (!pathname) return false;
  const clean = pathname.split("?")[0].split("#")[0].replace(/\/+$/, "") || "/";
  // Protected routes: /tasks, /tasks/*, /repositories, /repositories/*
  return /^\/(tasks|repositories)(\/|$)/.test(clean);
}

export function GitHubAuthProvider({ children }: { children: React.ReactNode }) {
  const { user, loading: authLoading } = useAuth();
  const [isAuthorized, setIsAuthorized] = useState<boolean>(false);
  const [hasInstalledApp, setHasInstalledApp] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [profile, setProfile] = useState<MeResponse | null>(null);
  const [repositories, setRepositories] = useState<RepositoryView[]>([]);
  const [isAuthModalOpen, setIsAuthModalOpen] = useState<boolean>(false);
  const [pendingDestination, setPendingDestination] = useState<string | null>(null);

  const activeCheckRef = useRef<Promise<boolean> | null>(null);

  const checkAuth = useCallback(async (): Promise<boolean> => {
    // 1. If user is not signed into Firebase, they cannot have an authorized backend session
    if (!user) {
      setIsAuthorized(false);
      setHasInstalledApp(false);
      setProfile(null);
      setRepositories([]);
      setLoading(false);
      return false;
    }

    // 2. Check if a backend session token exists
    const token = getAuthToken();
    if (!token) {
      setIsAuthorized(false);
      setHasInstalledApp(false);
      setProfile(null);
      setRepositories([]);
      setLoading(false);
      return false;
    }

    // If an existing check is in flight, reuse it
    if (activeCheckRef.current) {
      return activeCheckRef.current;
    }

    const runCheck = async (): Promise<boolean> => {
      setLoading(true);
      setError(null);

      try {
        // Validate session token with backend /me endpoint
        const me = await getMe();
        setProfile(me);
        setIsAuthorized(true);

        // Fetch accessible repositories. Note: The backend stores installation_ids
        // during OAuth callback, but MeResponse does not expose an installation query endpoint.
        // Therefore, items reflect accessible repositories with write permissions.
        // If items is empty, an authorized installation may still exist with 0 selected repositories.
        try {
          const reposRes = await listRepositories();
          const items = reposRes.items || [];
          setRepositories(items);
          // Set to true if repositories are accessible; if zero, user is still authorized
          setHasInstalledApp(items.length > 0);
        } catch {
          setRepositories([]);
        }

        setLoading(false);
        return true;
      } catch (err: unknown) {
        setProfile(null);
        setRepositories([]);
        setIsAuthorized(false);
        setHasInstalledApp(false);

        if (err instanceof ApiError && err.status === 401) {
          clearAuthToken();
          setError("Your GitHub session has expired. Please authorize again.");
        } else if (err instanceof Error) {
          setError(err.message);
        } else {
          setError("Unable to verify GitHub authorization status.");
        }

        setLoading(false);
        return false;
      } finally {
        activeCheckRef.current = null;
      }
    };

    activeCheckRef.current = runCheck();
    return activeCheckRef.current;
  }, [user]);

  useEffect(() => {
    if (authLoading) return;
    const timer = setTimeout(() => {
      void checkAuth();
    }, 0);

    const onAuthChange = () => {
      void checkAuth();
    };

    window.addEventListener("auth_change", onAuthChange);
    window.addEventListener("storage", onAuthChange);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("auth_change", onAuthChange);
      window.removeEventListener("storage", onAuthChange);
    };
  }, [authLoading, checkAuth]);

  // Check URL query params for redirect requests from route guards
  useEffect(() => {
    if (typeof window === "undefined") return;
    const timer = setTimeout(() => {
      const params = new URLSearchParams(window.location.search);
      if (params.get("auth_required") === "true") {
        const redirectTarget = params.get("redirect") || "/tasks";
        setPendingDestination(redirectTarget);
        try {
          sessionStorage.setItem("gitresolve_pending_redirect", redirectTarget);
        } catch {
          // ignore storage errors
        }
        setIsAuthModalOpen(true);
        // Clean up URL query parameters without reloading
        const url = new URL(window.location.href);
        url.searchParams.delete("auth_required");
        url.searchParams.delete("redirect");
        window.history.replaceState(null, "", url.pathname + (url.search ? url.search : ""));
      }
    }, 0);
    return () => clearTimeout(timer);
  }, []);

  const openAuthModal = useCallback((options?: { returnUrl?: string; message?: string }) => {
    if (options?.returnUrl) {
      setPendingDestination(options.returnUrl);
      try {
        sessionStorage.setItem("gitresolve_pending_redirect", options.returnUrl);
      } catch {
        // ignore storage errors
      }
    }
    setIsAuthModalOpen(true);
  }, []);

  const closeAuthModal = useCallback(() => {
    setIsAuthModalOpen(false);
  }, []);

  return (
    <GitHubAuthContext.Provider
      value={{
        isAuthorized,
        hasInstalledApp,
        loading: authLoading || loading,
        error,
        profile,
        repositories,
        checkAuth,
        openAuthModal,
        closeAuthModal,
        isAuthModalOpen,
        pendingDestination,
      }}
    >
      {children}
    </GitHubAuthContext.Provider>
  );
}

export function useGitHubAuth() {
  return useContext(GitHubAuthContext);
}
