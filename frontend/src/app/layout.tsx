import type { Metadata } from "next";
import { Suspense } from "react";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { Providers } from "@/components/Providers";
import { AppShell } from "@/components/AppShell";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_APP_URL || "https://gitresolve-agent.vercel.app"),
  title: { default: "GitResolve — Ship the fix. Keep the veto.", template: "%s | GitResolve" },
  description: "From GitHub issue to tested fix. Investigate, repair, and review every pull request before it leaves.",
  icons: {
    icon: [
      { url: "/gitresolve-favicon.svg", type: "image/svg+xml" },
      { url: "/gitresolve-logo.png", type: "image/png" },
    ],
    apple: "/apple-icon.png",
  },
  openGraph: {
    title: "GitResolve — Ship the fix. Keep the veto.",
    description: "From GitHub issue to tested fix. Investigate, repair, and review every pull request before it leaves.",
    url: "https://gitresolve-agent.vercel.app",
    siteName: "GitResolve",
    images: [
      {
        url: "/gitresolve-og.png",
        width: 1200,
        height: 630,
        alt: "GitResolve — Ship the fix. Keep the veto.",
        type: "image/png",
      },
    ],
    locale: "en_US",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "GitResolve — Ship the fix. Keep the veto.",
    description: "From GitHub issue to tested fix. Investigate, repair, and review every pull request before it leaves.",
    images: ["/gitresolve-og.png"],
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body>
        <Providers>
          <Suspense fallback={<div className="page-loading" role="status">Opening workspace…</div>}>
            <AppShell>{children}</AppShell>
          </Suspense>
        </Providers>
      </body>
    </html>
  );
}
