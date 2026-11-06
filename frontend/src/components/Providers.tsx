"use client";
import {SmoothScroll} from "./SmoothScroll";
import { useState } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { apiQueryClient } from "@/lib/query-client";
import { MotionConfig } from "motion/react";
import { Toaster } from "sonner";
import { ThemeProvider } from "@/context/ThemeContext";
import { AuthProvider } from "@/context/AuthContext";
export function Providers({ children }: { children: React.ReactNode }) {
 const [client] = useState(() => apiQueryClient);
 return <ThemeProvider><QueryClientProvider client={client}><MotionConfig reducedMotion="user"><AuthProvider><SmoothScroll>{children}</SmoothScroll><Toaster position="bottom-right" toastOptions={{ className: "glass-toast" }} /></AuthProvider></MotionConfig></QueryClientProvider></ThemeProvider>;
}
