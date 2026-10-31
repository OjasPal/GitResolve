"use client";

import { useEffect, useState } from "react";
import {
  checkBackendHealth,
  clearAuthToken,
  getAuthToken,
  getMe,
  loginWithDevToken,
} from "@/lib/api";
import { MeResponse } from "@/types";
import {
  AlertCircle,
  CheckCircle2,
  Key,
  LogOut,
  RefreshCw,
  Server,
  Settings,
  ShieldCheck,
  User,
  UserCheck,
  Wifi,
} from "lucide-react";

export default function SettingsPage() {
  const [user, setUser] = useState<MeResponse | null>(null);
  const [backendHealth, setBackendHealth] = useState<{ status: string }>({ status: "checking" });
  const [devUserId, setDevUserId] = useState("123456");
  const [devLogin, setDevLogin] = useState("dev_aakash");
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const sync = async () => {
    const health = await checkBackendHealth();
    setBackendHealth(health);
    const token = getAuthToken();
    if (token) {
      try {
        const u = await getMe();
        setUser(u);
        setDevUserId(u.id);
        setDevLogin(u.githubLogin);
      } catch {
        setUser(null);
      }
    } else {
      setUser(null);
    }
  };

  useEffect(() => {
    let active = true;
    checkBackendHealth().then((health) => { if (active) setBackendHealth(health); });
    if (getAuthToken()) {
      getMe().then((profile) => {
        if (active) { setUser(profile); setDevUserId(profile.id); setDevLogin(profile.githubLogin); }
      }).catch(() => { if (active) setUser(null); });
    }
    return () => { active = false; };
  }, []);

  const handleDevLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setLoading(true);
      setMessage(null);
      await loginWithDevToken(devUserId, devLogin);
      await sync();
      setMessage("Authenticated with development session token successfully!");
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : "Failed to generate token");
    } finally {
      setLoading(false);
    }
  };

  const handleSignOut = () => {
    clearAuthToken();
    setUser(null);
    setMessage("Signed out. Cleared local session token.");
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2.5">
          <Settings className="w-6 h-6 text-sky-400" />
          <span>Platform Settings & Identity</span>
        </h1>
        <p className="text-sm text-slate-400 mt-0.5">
          Manage your GitHub authentication status, development session tokens, and connection configurations
        </p>
      </div>

      {message && (
        <div className="p-4 rounded-xl bg-slate-900 border border-sky-500/50 text-sky-300 text-xs flex items-center gap-2.5">
          <CheckCircle2 className="w-4 h-4 text-sky-400 shrink-0" />
          <span>{message}</span>
        </div>
      )}

      {/* Backend Status Card */}
      <div className="p-6 rounded-2xl bg-slate-900/60 border border-slate-800 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-slate-800 flex items-center justify-center text-sky-400">
              <Server className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-white">Backend API Server</h3>
              <p className="text-xs text-slate-400 font-mono">
                {process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000/api/v1"}
              </p>

            </div>
          </div>

          <span
            className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-mono font-bold border ${
              backendHealth.status === "ok"
                ? "bg-emerald-950/60 border-emerald-800 text-emerald-300"
                : "bg-rose-950/60 border-rose-800 text-rose-300"
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                backendHealth.status === "ok" ? "bg-emerald-400 animate-ping" : "bg-rose-400"
              }`}
            />
            <span>{backendHealth.status.toUpperCase()}</span>
          </span>
        </div>
      </div>

      {/* Current User Card */}
      <div className="p-6 rounded-2xl bg-slate-900/60 border border-slate-800 space-y-4">
        <h2 className="text-xs font-bold uppercase tracking-wider text-slate-400 flex items-center gap-2">
          <UserCheck className="w-4 h-4 text-sky-400" />
          <span>Active Session Details</span>
        </h2>

        {user ? (
          <div className="flex items-center justify-between p-4 rounded-xl bg-slate-950 border border-slate-800">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center text-sky-400 font-bold overflow-hidden">
                {user.avatarUrl ? (
                  <img src={user.avatarUrl} alt={user.githubLogin} className="w-full h-full object-cover" />
                ) : (
                  <User className="w-5 h-5" />
                )}
              </div>
              <div>
                <p className="text-sm font-bold text-white flex items-center gap-2">
                  <span>{user.githubLogin}</span>
                  <span className="px-1.5 py-0.2 rounded bg-sky-500/20 text-sky-400 text-[10px] font-mono">
                    ID: {user.id}
                  </span>
                </p>
                <p className="text-xs text-slate-400 mt-0.5">
                  {user.activeTaskId ? (
                    <span className="text-amber-400">Active Task: {user.activeTaskId}</span>
                  ) : (
                    <span className="text-emerald-400">Active Task Slot: Available (0/1)</span>
                  )}
                </p>
              </div>
            </div>

            <button
              onClick={handleSignOut}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-rose-950/60 border border-slate-700 hover:border-rose-800 text-slate-300 hover:text-rose-300 text-xs font-medium transition-all cursor-pointer"
            >
              <LogOut className="w-3.5 h-3.5" />
              <span>Sign Out</span>
            </button>
          </div>
        ) : (
          <div className="p-4 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-400">
            No active session found. Use the dev session generator below to mint a local session token.
          </div>
        )}
      </div>

      {/* Dev Token Minting Form */}
      <form onSubmit={handleDevLogin} className="p-6 rounded-2xl bg-slate-900/60 border border-slate-800 space-y-4">
        <div>
          <h2 className="text-xs font-bold uppercase tracking-wider text-slate-400 flex items-center gap-2">
            <Key className="w-4 h-4 text-purple-400" />
            <span>Developer Local Session Switcher</span>
          </h2>
          <p className="text-xs text-slate-500 mt-1">
            Calls <code className="text-sky-400">POST /api/v1/auth/dev-token</code> to simulate different GitHub users locally.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-slate-300">User ID (numeric string):</label>
            <input
              type="text"
              value={devUserId}
              onChange={(e) => setDevUserId(e.target.value)}
              className="w-full px-3.5 py-2 rounded-xl bg-slate-950 border border-slate-800 text-slate-200 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-sky-500/40"
              required
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-slate-300">GitHub Login Username:</label>
            <input
              type="text"
              value={devLogin}
              onChange={(e) => setDevLogin(e.target.value)}
              className="w-full px-3.5 py-2 rounded-xl bg-slate-950 border border-slate-800 text-slate-200 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-sky-500/40"
              required
            />
          </div>
        </div>

        <div className="pt-2 flex justify-end">
          <button
            type="submit"
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-purple-600 hover:bg-purple-500 text-white font-semibold text-xs shadow-md shadow-purple-900/40 cursor-pointer disabled:opacity-50"
          >
            {loading ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5" />}
            <span>Mint Dev Session Token</span>
          </button>
        </div>
      </form>
    </div>
  );
}
