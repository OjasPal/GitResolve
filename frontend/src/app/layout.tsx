import type { Metadata } from "next";
import { Suspense } from "react";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { Providers } from "@/components/Providers";
import { AppShell } from "@/components/AppShell";
import "./globals.css";
export const metadata: Metadata = {
 title: { default: "GitResolve — Ship the fix. Keep the veto.", template: "%s | GitResolve" },
 description: "From GitHub issue to tested fix. Investigate, repair, and review every pull request before it leaves.",
 icons: { icon: "/gitresolve-favicon.svg" },
 openGraph: { title: "GitResolve — Ship the fix. Keep the veto.", description: "Autonomous engineering, human final say.", images: [{ url: "/gitresolve-og.png", width: 1200, height: 630 }], type: "website" },
 twitter: { card: "summary_large_image", images: ["/gitresolve-og.png"] },
};
export default function RootLayout({ children }: { children: React.ReactNode }) {
 return <html lang="en" suppressHydrationWarning className={`${GeistSans.variable} ${GeistMono.variable}`}><body><Providers><Suspense fallback={<div className="page-loading" role="status">Opening workspace…</div>}><AppShell>{children}</AppShell></Suspense></Providers></body></html>;
}
