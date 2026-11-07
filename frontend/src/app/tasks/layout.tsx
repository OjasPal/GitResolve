"use client";

import React from "react";
import { GitHubProtectedRoute } from "@/components/GitHubProtectedRoute";

export default function TasksLayout({ children }: { children: React.ReactNode }) {
  return <GitHubProtectedRoute>{children}</GitHubProtectedRoute>;
}
