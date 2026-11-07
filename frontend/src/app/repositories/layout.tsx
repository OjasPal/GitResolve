"use client";

import React from "react";
import { GitHubProtectedRoute } from "@/components/GitHubProtectedRoute";

export default function RepositoriesLayout({ children }: { children: React.ReactNode }) {
  return <GitHubProtectedRoute>{children}</GitHubProtectedRoute>;
}
