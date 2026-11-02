"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/context/AuthContext";
import { useTheme } from "@/context/ThemeContext";
import { checkBackendHealth } from "@/lib/api";
import {
  FolderGit2,
  LayoutDashboard,
  ListTodo,
  Moon,
  Plus,
  Settings,
  Sun,
  User} from "lucide-react";

export function Navbar() {
  const pathname = usePathname();
  const { user } = useAuth();
  const { theme, toggleTheme } = useTheme();

  const [backendStatus, setBackendStatus] = useState<string>("checking");

  useEffect(() => {
    checkBackendHealth().then((h) => setBackendStatus(h.status));
    const timer = setInterval(() => {
      checkBackendHealth().then((h) => setBackendStatus(h.status));
    }, 20000);
    return () => clearInterval(timer);
  }, []);

  const navLinks = [
    { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
    { href: "/tasks", label: "Tasks", icon: ListTodo },
    { href: "/repositories", label: "Repositories", icon: FolderGit2 },
    { href: "/settings", label: "Settings", icon: Settings },
  ];

  return (
    <header className="sticky top-0 z-40 border-b border-neutral-200 dark:border-neutral-800 bg-white/90 dark:bg-neutral-950/90 backdrop-blur-md">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-14 flex items-center justify-between gap-4">
        {/* Brand */}
        <div className="flex items-center gap-6">
          <Link
            href={user ? "/dashboard" : "/"}
            className="flex items-center gap-2 text-neutral-900 dark:text-neutral-100 font-bold text-base tracking-tight hover:opacity-90"
          >
            <div className="w-7 h-7 rounded-lg border border-neutral-800 bg-neutral-900 text-neutral-100 dark:border-neutral-200 dark:bg-neutral-100 dark:text-neutral-900 flex items-center justify-center font-mono font-bold text-xs">
              GR
            </div>
            <span>GitResolve</span>
          </Link>

          {/* Navigation Links for Authenticated Users */}
          {user && (
            <nav className="hidden md:flex items-center gap-1">
              {navLinks.map((item) => {
                const Icon = item.icon;
                const isActive = pathname === item.href || (item.href !== "/dashboard" && pathname.startsWith(item.href));
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`px-3 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1.5 transition-colors ${
                      isActive
                        ? "bg-neutral-100 dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 font-semibold"
                        : "text-neutral-600 dark:text-neutral-400 hover:text-neutral-900 dark:hover:text-neutral-100 hover:bg-neutral-50 dark:hover:bg-neutral-900"
                    }`}
                  >
                    <Icon className="w-3.5 h-3.5" />
                    <span>{item.label}</span>
                  </Link>
                );
              })}
            </nav>
          )}
        </div>

        {/* Right side controls */}
        <div className="flex items-center gap-2.5">
          {/* Backend Status indicator */}
          <div
            className="hidden sm:inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] font-mono border border-neutral-200 dark:border-neutral-800 text-neutral-500"
            title={`FastAPI backend status: ${backendStatus}`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                backendStatus === "ok" || backendStatus === "healthy"
                  ? "bg-emerald-500"
                  : "bg-amber-500"
              }`}
            />
            <span>API {backendStatus === "ok" ? "OK" : backendStatus}</span>
          </div>

          {/* Theme toggle */}
          <button
            type="button"
            onClick={toggleTheme}
            className="p-1.5 rounded-lg border border-neutral-300 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-600 dark:text-neutral-300 transition-colors cursor-pointer"
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
            title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
          >
            {theme === "dark" ? (
              <Sun className="w-4 h-4" />
            ) : (
              <Moon className="w-4 h-4" />
            )}
          </button>

          {/* User controls */}
          {user ? (
            <div className="flex items-center gap-2">
              <Link
                href="/tasks/new"
                className="hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-neutral-900 hover:bg-neutral-800 dark:bg-neutral-100 dark:hover:bg-neutral-200 text-white dark:text-neutral-900 text-xs font-semibold transition-colors"
              >
                <Plus className="w-3.5 h-3.5 stroke-[2.5]" />
                <span>New Repair</span>
              </Link>

              <Link
                href="/settings"
                className="w-7 h-7 rounded-full bg-neutral-200 dark:bg-neutral-800 flex items-center justify-center text-xs font-semibold text-neutral-700 dark:text-neutral-300 hover:ring-2 hover:ring-neutral-400 dark:hover:ring-neutral-600 transition-all"
                title={user.email || user.displayName || "Settings"}
              >
                {user.displayName ? (
                  user.displayName.slice(0, 1).toUpperCase()
                ) : user.email ? (
                  user.email.slice(0, 1).toUpperCase()
                ) : (
                  <User className="w-3.5 h-3.5" />
                )}
              </Link>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <Link
                href="/login"
                className="px-3 py-1.5 rounded-lg text-xs font-medium text-neutral-700 dark:text-neutral-300 hover:text-neutral-900 dark:hover:text-neutral-100 hover:bg-neutral-100 dark:hover:bg-neutral-800 transition-colors"
              >
                Sign In
              </Link>
              <Link
                href="/register"
                className="px-3.5 py-1.5 rounded-lg bg-neutral-900 hover:bg-neutral-800 dark:bg-neutral-100 dark:hover:bg-neutral-200 text-white dark:text-neutral-900 text-xs font-semibold transition-colors"
              >
                Get Started
              </Link>
            </div>
          )}
        </div>
      </div>
      {user && <nav aria-label="Mobile navigation" className="md:hidden flex overflow-x-auto border-t border-neutral-200 dark:border-neutral-800 px-4 py-2 gap-4 text-xs">{navLinks.map((item) => <Link key={item.href} href={item.href} aria-current={pathname === item.href ? "page" : undefined} className="whitespace-nowrap">{item.label}</Link>)}<Link href="/tasks/new" className="whitespace-nowrap">New Repair</Link></nav>}
    </header>
  );
}
