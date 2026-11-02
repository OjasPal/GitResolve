"use client";
import { ThemeProvider as NextThemeProvider, useTheme as useNextTheme } from "next-themes";
export function ThemeProvider({ children }: { children: React.ReactNode }) {
 return <NextThemeProvider attribute="class" defaultTheme="dark" enableSystem storageKey="gitresolve_theme" disableTransitionOnChange>{children}</NextThemeProvider>;
}
/** Preserve the existing consumer API; next-themes owns persistence and system mode. */
export function useTheme() {
 const { theme, resolvedTheme, setTheme } = useNextTheme();
 return { theme: resolvedTheme || "dark", preference: theme || "dark", setTheme,
 toggleTheme: () => setTheme(resolvedTheme === "light" ? "dark" : "light") };
}
