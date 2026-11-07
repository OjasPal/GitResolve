"use client";

import React, { createContext, useContext, useEffect, useState } from "react";
import {
  User,
  onIdTokenChanged,
  signInWithPopup,
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  signOut,
  sendPasswordResetEmail,
  updateProfile} from "firebase/auth";
import { auth, googleProvider, githubProvider, isFirebaseConfigured } from "@/lib/firebase";
import { clearAuthToken } from "@/lib/api";

export interface GitResolveUser {
  uid: string;
  email: string | null;
  displayName: string | null;
  photoURL: string | null;
  providerId: string;
}

interface AuthContextType {
  user: GitResolveUser | null;
  loading: boolean;
  isConfigured: boolean;
  error: string | null;
  signInWithGoogle: () => Promise<void>;
  signInWithGitHub: () => Promise<void>;
  signInWithEmail: (email: string, pass: string) => Promise<void>;
  signUpWithEmail: (email: string, pass: string, name?: string) => Promise<void>;
  updateDisplayName: (name: string) => Promise<void>;
  resetPassword: (email: string) => Promise<void>;
  signOutUser: () => Promise<void>;
  getIdToken: () => Promise<string | null>;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  loading: true,
  isConfigured: false,
  error: null,
  signInWithGoogle: async () => {},
  signInWithGitHub: async () => {},
  signInWithEmail: async () => {},
  signUpWithEmail: async () => {},
  updateDisplayName: async () => {},
  resetPassword: async () => {},
  signOutUser: async () => {},
  getIdToken: async () => null,
});



export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<GitResolveUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!auth) {
      const restoreSession = () => {
        const owner = typeof window !== "undefined" ? localStorage.getItem("gitresolve_session_owner") : null;
        const token = typeof window !== "undefined" ? (localStorage.getItem("gitresolve_access_token") || sessionStorage.getItem("gitresolve_access_token")) : null;
        if (owner && token) {
          setUser({
            uid: owner,
            email: `${owner}@gitresolve.dev`,
            displayName: owner,
            photoURL: null,
            providerId: "password",
          });
        } else {
          setUser(null);
        }
        setLoading(false);
      };
      restoreSession();
      window.addEventListener("auth_change", restoreSession);
      window.addEventListener("storage", restoreSession);
      return () => {
        window.removeEventListener("auth_change", restoreSession);
        window.removeEventListener("storage", restoreSession);
      };
    }
    return onIdTokenChanged(auth, (firebaseUser: User | null) => {
      const owner = localStorage.getItem("gitresolve_session_owner");
      if (!firebaseUser || owner !== firebaseUser.uid) clearAuthToken();
      setUser(firebaseUser ? {
        uid: firebaseUser.uid,
        email: firebaseUser.email,
        displayName: firebaseUser.displayName,
        photoURL: firebaseUser.photoURL,
        providerId: firebaseUser.providerData[0]?.providerId || "password",
      } : null);
      setLoading(false);
    }, () => {
      setError("Unable to restore your sign-in. Please sign in again.");
      setLoading(false);
    });
  }, []);

  const signInWithGitHub = async () => {
    setError(null);
    if (!auth || !githubProvider) throw new Error("GitHub sign-in is currently unavailable.");
    try { await signInWithPopup(auth, githubProvider); }
    catch (err) {
      const code = (err as { code?: string }).code;
      const message = code === "auth/account-exists-with-different-credential"
        ? "An account already exists with this email. Sign in using your original method, then connect GitHub from your workspace."
        : code === "auth/popup-closed-by-user" ? "Sign-in was cancelled. Try again when you’re ready."
        : "GitHub sign-in failed. Please try again or use another sign-in method.";
      setError(message); throw new Error(message);
    }
  };

  const signInWithGoogle = async () => {
    setError(null);
    if (!isFirebaseConfigured || !auth || !googleProvider) {
      throw new Error("Firebase Authentication is not configured. Please add Firebase credentials to frontend/.env.local.");
    }
    try {
      await signInWithPopup(auth, googleProvider);

    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Google sign-in failed.";
      setError(msg);
      throw err;
    }
  };

  const signInWithEmail = async (email: string, pass: string) => {
    setError(null);
    if (!isFirebaseConfigured || !auth) {
      throw new Error("Firebase Authentication is not configured. Please add Firebase credentials to frontend/.env.local.");
    }
    try {
      await signInWithEmailAndPassword(auth, email, pass);

    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Email sign-in failed.";
      setError(msg);
      throw err;
    }
  };

  const signUpWithEmail = async (email: string, pass: string, name?: string) => {
    setError(null);
    if (!isFirebaseConfigured || !auth) {
      throw new Error("Firebase Authentication is not configured. Please add Firebase credentials to frontend/.env.local.");
    }
    try {
      const cred = await createUserWithEmailAndPassword(auth, email, pass);
      if (name && cred.user) {
        await updateProfile(cred.user, { displayName: name });
        setUser({ uid: cred.user.uid, email: cred.user.email, displayName: name, photoURL: cred.user.photoURL, providerId: "password" });
      }

    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Registration failed.";
      setError(msg);
      throw err;
    }
  };

  const updateDisplayName = async (name: string) => {
    const displayName = name.trim();
    if (!displayName || displayName.length > 80) {
      throw new Error("Enter a display name between 1 and 80 characters.");
    }
    const account = auth?.currentUser;
    if (!account) throw new Error("Sign in again to update your display name.");
    await updateProfile(account, { displayName });
    setUser(current => current?.uid === account.uid ? { ...current, displayName } : current);
  };

  const resetPassword = async (email: string) => {
    setError(null);
    if (!isFirebaseConfigured || !auth) {
      throw new Error("Firebase Authentication is not configured. Please add Firebase credentials to frontend/.env.local.");
    }
    try {
      await sendPasswordResetEmail(auth, email);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Password reset failed.";
      setError(msg);
      throw err;
    }
  };

  const signOutUser = async () => {
    setError(null);
    localStorage.removeItem("gitresolve_session_owner");
    clearAuthToken();
    if (auth) {
      try {
        await signOut(auth);
      } catch (err) {
        setError("Unable to sign out. Please try again.");
        throw err;
      }
    }
    setUser(null);
  };

  const getIdToken = async (): Promise<string | null> => {
    if (auth?.currentUser) {
      return auth.currentUser.getIdToken();
    }
    return null;
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        isConfigured: isFirebaseConfigured,
        error,
        signInWithGoogle,
        signInWithGitHub,
        signInWithEmail,
        signUpWithEmail,
        updateDisplayName,
        resetPassword,
        signOutUser,
        getIdToken,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
