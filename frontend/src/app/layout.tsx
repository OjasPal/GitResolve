import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Suspense } from "react";
import { Navbar } from "@/components/Navbar";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "GitResolve — AI-Powered Automated Bug Fixer",
  description:
    "Autonomous GitHub issue investigator and automated code repair platform powered by LangGraph, FastAPI, and Docker execution sandbox.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased dark`}
    >
      <body className="min-h-full flex flex-col bg-slate-950 text-slate-100 font-sans selection:bg-sky-500/20 selection:text-sky-300">
        <Suspense fallback={<div className="h-16 border-b border-slate-900 bg-slate-950" />}>
          <Navbar />
        </Suspense>
        <p className="text-center text-xs text-amber-300 p-2">Development prototype: repair execution and GitHub App publishing are not yet implemented.</p>
        <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6">
          {children}
        </main>
        <footer className="w-full border-t border-slate-900 py-6 text-center text-xs text-slate-600">
          GitResolve — Autonomous Agentic Coding Platform · Member 1 (AI) · Member 2 (Backend) · Member 3 (Sandbox) · Member 4 (Frontend)
        </footer>
      </body>
    </html>
  );
}
