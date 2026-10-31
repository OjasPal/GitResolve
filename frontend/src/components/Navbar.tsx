"use client";

import { useEffect, useState } from "react";
import Link from "next/navigation";
import { usePathname } from "next/navigation";
import NextLink from "next/link";
import {
  checkBackendHealth,
  clearAuthToken,
  ensureDevAuthenticated,
  getAuthToken,
  getMe,
  getStoredUser,
} from "@/lib/api";
import { MeResponse } from "@/types";
import {
  Activity,
  Bot,
  CheckCircle2,
  FolderGit2,
  GitBranch,
  LayoutDashboard,
  ListTodo,
  LogOut,
  PlusCircle,
  Settings,
  ShieldCheck,
  User,
  Wifi,
  WifiOff,
} from "lucide-react";

export function Navbar() {
  const pathname = usePathname();
  const [backendStatus, setBackendStatus] = useState<string>("checking");
  const [user, setUser] = useState<MeResponse | null>(null);
  const [tokenPresent, setTokenPresent] = useState<boolean>(false);

  useEffect(() => {
    // Initial health check
    checkBackendHealth().then((h) => setBackendStatus(h.status));
    const healthInterval = setInterval(() => {
      checkBackendHealth().then((h) => setBackendStatus(h.status));
    }, 15000);

    // Initial auth sync
    const syncAuth = async () => {
      const token = getAuthToken();
      setTokenPresent(!!token);
      if (token) {
        try {
          const profile = await getMe();
          setUser(profile);
        } catch {
          // Token invalid or offline
        }
      } else {
        // Auto sign-in in dev mode
        try {
          await ensureDevAuthenticated();
          setTokenPresent(true);
          const profile = await getMe();
          setUser(profile);
        } catch {
          // backend not reachable
        }
      }
    };

    syncAuth();

    const handleAuthChange = () => {
      syncAuth();
    };

    window.addEventListener("auth_change", handleAuthChange);
    return () => {
      clearInterval(healthInterval);
      window.removeEventListener("auth_change", handleAuthChange);
    };
  }, []);

  const navItems = [
    { href: "/", label: "Dashboard", icon: LayoutDashboard },
    { href: "/tasks/new", label: "New Repair", icon: PlusCircle, highlight: true },
    { href: "/tasks", label: "Tasks", icon: ListTodo },
    { href: "/repositories", label: "Repositories", icon: FolderGit2 },
    { href: "/settings", label: "Settings", icon: Settings },
  ];

  return (
    <header className="sticky top-0 z-50 w-full border-b border-slate-800/80 bg-slate-950/80 backdrop-blur-md">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          {/* Brand Logo */}
          <div className="flex items-center gap-8">
            <NextLink href="/" className="flex items-center gap-2.5 group">
              <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-sky-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-sky-500/20 group-hover:scale-105 transition-transform">
                <Bot className="w-5 h-5 text-slate-950 stroke-[2.5]" />
              </div>
              <div className="flex flex-col">
                <div className="flex items-center gap-1.5">
                  <span className="font-bold text-base tracking-tight text-white">GitResolve</span>
                  <span className="px-1.5 py-0.2 rounded bg-sky-500/20 border border-sky-400/30 text-[10px] font-bold text-sky-400">
                    AI AGENT
                  </span>
                </div>
                <span className="text-[10px] text-slate-400 tracking-wide font-medium">
                  Automated Bug Fixer
                </span>
              </div>
            </NextLink>

            {/* Desktop Navigation Links */}
            <nav className="hidden md:flex items-center gap-1">
              {navItems.map((item) => {
                const Icon = item.icon;
                const isActive = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href));

                if (item.highlight) {
                  return (
                    <NextLink
                      key={item.href}
                      href={item.href}
                      className="ml-2 flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-sky-500 hover:bg-sky-400 text-slate-950 text-xs font-semibold shadow-sm shadow-sky-500/30 transition-all cursor-pointer"
                    >
                      <Icon className="w-3.5 h-3.5 stroke-[2.5]" />
                      <span>{item.label}</span>
                    </NextLink>
                  );
                }

                return (
                  <NextLink
                    key={item.href}
                    href={item.href}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                      isActive
                        ? "bg-slate-800 text-white font-semibold shadow-inner"
                        : "text-slate-400 hover:text-slate-200 hover:bg-slate-900"
                    }`}
                  >
                    <Icon className="w-3.5 h-3.5" />
                    <span>{item.label}</span>
                  </NextLink>
                );
              })}
            </nav>
          </div>

          {/* Right Area: Backend Status + Auth Status */}
          <div className="flex items-center gap-4">
            {/* Backend Connectivity Badge */}
            <div
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-mono border ${
                backendStatus === "ok"
                  ? "bg-emerald-950/40 border-emerald-800/60 text-emerald-300"
                  : backendStatus === "checking"
                  ? "bg-slate-900 border-slate-800 text-slate-400"
                  : "bg-rose-950/50 border-rose-800/80 text-rose-300"
              }`}
              title={
                backendStatus === "ok"
                  ? "Backend is connected & healthy"
                  : "Backend disconnected - check port 8001"
              }

            >
              {backendStatus === "ok" ? (
                <>
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping" />
                  <span className="hidden sm:inline">Backend API:</span>
                  <span>ONLINE</span>
                </>
              ) : backendStatus === "checking" ? (
                <>
                  <span className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-pulse" />
                  <span>Connecting...</span>
                </>
              ) : (
                <>
                  <WifiOff className="w-3 h-3 text-rose-400" />
                  <span>DISCONNECTED</span>
                </>
              )}
            </div>

            {/* User Profile / Dev Session */}
            <div className="flex items-center gap-2 pl-2 border-l border-slate-800">
              {user ? (
                <div className="flex items-center gap-2">
                  <div className="w-7 h-7 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center text-xs font-bold text-sky-400 overflow-hidden">
                    {user.avatarUrl ? (
                      <img src={user.avatarUrl} alt={user.githubLogin} className="w-full h-full object-cover" />
                    ) : (
                      <User className="w-4 h-4 text-slate-300" />
                    )}
                  </div>
                  <span className="hidden sm:inline text-xs font-medium text-slate-300">
                    {user.githubLogin}
                  </span>
                </div>
              ) : (
                <NextLink
                  href="/settings"
                  className="flex items-center gap-1.5 text-xs text-sky-400 hover:underline"
                >
                  <ShieldCheck className="w-3.5 h-3.5" />
                  <span>Dev Auth</span>
                </NextLink>
              )}
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}
