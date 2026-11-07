"use client";
import {SmoothScroll} from "./SmoothScroll";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "motion/react";
import { Toaster } from "sonner";
import { ThemeProvider } from "@/context/ThemeContext";
import { AuthProvider } from "@/context/AuthContext";
import { GitHubAuthProvider } from "@/context/GitHubAuthContext";

export function Providers({ children }: { children: React.ReactNode }) {
 const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } }));
 return (
   <ThemeProvider>
     <QueryClientProvider client={client}>
       <MotionConfig reducedMotion="user">
         <AuthProvider>
           <GitHubAuthProvider>
             <SmoothScroll>{children}</SmoothScroll>
             <Toaster position="bottom-right" toastOptions={{ className: "glass-toast" }} />
           </GitHubAuthProvider>
         </AuthProvider>
       </MotionConfig>
     </QueryClientProvider>
   </ThemeProvider>
 );
}
