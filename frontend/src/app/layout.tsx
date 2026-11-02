import type { Metadata } from "next";
import { Suspense } from "react";
import { AppAccess } from "@/components/AppAccess";
import { Navbar } from "@/components/Navbar";
import { AuthProvider } from "@/context/AuthContext";
import { ThemeProvider } from "@/context/ThemeContext";
import "./globals.css";

export const metadata: Metadata = {
  title: "GitResolve — Autonomous GitHub Bug Investigator & Repair Platform",
  description:
    "AI-powered code repair platform orchestrating LangGraph multi-model routing, isolated Docker execution sandboxes, and human-in-the-loop pull request preparation.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className="h-full antialiased dark"
    >
      <head><script dangerouslySetInnerHTML={{ __html: `try{document.documentElement.classList.toggle('dark',localStorage.getItem('gitresolve_theme')!=='light')}catch{}` }} /></head>
      <body className="min-h-full flex flex-col bg-white dark:bg-neutral-950 text-neutral-900 dark:text-neutral-100 font-sans selection:bg-neutral-200 dark:selection:bg-neutral-800">
        <ThemeProvider>
          <AuthProvider>
            <Suspense fallback={<div className="h-14 border-b border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950" />}>
              <Navbar />
            </Suspense>

            <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6">
              <Suspense fallback={<p className="py-16 text-center">Loading...</p>}><AppAccess>{children}</AppAccess></Suspense>
            </main>

            <footer className="w-full border-t border-neutral-200 dark:border-neutral-800 py-6 text-center text-xs text-neutral-500 font-mono">
              GitResolve · Autonomous Engineering Platform · Pull Requests Require Human Maintainer Approval
            </footer>
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
