"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ensureDevAuthenticated, listTasks } from "@/lib/api";
import { TaskStatus, TaskSummaryItem } from "@/types";
import { StatusBadge } from "@/components/StatusBadge";
import {
  ArrowRight,
  Filter,
  Inbox,
  ListTodo,
  Loader2,
  PlusCircle,
  RefreshCw,
  Search,
} from "lucide-react";

export default function TasksPage() {
  const [tasks, setTasks] = useState<TaskSummaryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [filterStatus, setFilterStatus] = useState<string>("all");

  const loadTasks = async () => {
    try {
      await ensureDevAuthenticated();
      const res = await listTasks(50);
      setTasks(res.items || []);
    } catch {
      // error handled
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let active = true;
    ensureDevAuthenticated().then(() => listTasks(50)).then((res) => {
      if (active) { setTasks(res.items || []);  }
    }).catch((err: unknown) => {
      if (active) {  }
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const filteredTasks = tasks.filter((t) => {
    const matchesSearch =
      t.repoFullName.toLowerCase().includes(search.toLowerCase()) ||
      String(t.issue.number).includes(search) ||
      (t.issue.title && t.issue.title.toLowerCase().includes(search.toLowerCase())) ||
      t.id.toLowerCase().includes(search.toLowerCase());

    const matchesStatus =
      filterStatus === "all" ||
      (filterStatus === "active" && ["queued", "planning", "running", "creating_pr"].includes(t.status)) ||
      (filterStatus === "review" && ["awaiting_start", "awaiting_approval"].includes(t.status)) ||
      (filterStatus === "completed" && t.status === "completed") ||
      (filterStatus === "failed" && (t.status.startsWith("failed_") || t.status === "timed_out"));

    return matchesSearch && matchesStatus;
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2.5">
            <ListTodo className="w-6 h-6 text-sky-400" />
            <span>Repair Tasks Directory</span>
          </h1>
          <p className="text-sm text-slate-400 mt-0.5">
            History of automated issue repairs, test runs, and pull request deployments
          </p>
        </div>

        <div className="flex items-center gap-2.5">
          <button
            onClick={() => { setLoading(true); void loadTasks(); }}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-900 border border-slate-800 text-slate-300 hover:text-white text-xs font-semibold cursor-pointer"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            <span>Refresh</span>
          </button>
          <Link
            href="/tasks/new"
            className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-sky-500 hover:bg-sky-400 text-slate-950 text-xs font-bold shadow-md shadow-sky-500/20 cursor-pointer"
          >
            <PlusCircle className="w-3.5 h-3.5" />
            <span>New Repair</span>
          </Link>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
        <div className="relative flex-1">
          <Search className="w-4 h-4 text-slate-500 absolute left-3.5 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Search by repo, issue title, or #number..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-10 pr-4 py-2 rounded-xl bg-slate-900/80 border border-slate-800 text-slate-200 text-xs focus:outline-none focus:ring-2 focus:ring-sky-500/40"
          />
        </div>

        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-slate-500" />
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            className="px-3 py-2 rounded-xl bg-slate-900/80 border border-slate-800 text-slate-300 text-xs focus:outline-none focus:ring-2 focus:ring-sky-500/40 cursor-pointer"
          >
            <option value="all">All Statuses ({tasks.length})</option>
            <option value="review">Needs Review (Gates 1 & 2)</option>
            <option value="active">Active Running</option>
            <option value="completed">Completed (PR)</option>
            <option value="failed">Failed / Timed Out</option>
          </select>
        </div>
      </div>

      {/* Task List Table */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/50 overflow-hidden shadow-lg">
        {loading ? (
          <div className="flex flex-col items-center justify-center p-12 text-slate-500">
            <Loader2 className="w-7 h-7 text-sky-400 animate-spin mb-3" />
            <p className="text-sm">Loading task directory...</p>
          </div>
        ) : filteredTasks.length === 0 ? (
          <div className="flex flex-col items-center justify-center p-12 text-slate-500 text-center">
            <Inbox className="w-10 h-10 mb-3 text-slate-600" />
            <h3 className="text-sm font-semibold text-slate-300 mb-1">No matching tasks found</h3>
            <p className="text-xs text-slate-500 max-w-sm">
              Try adjusting your search criteria or create a new repair task.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-950/60 text-slate-400 uppercase text-[10px] tracking-wider border-b border-slate-800">
                <tr>
                  <th className="py-3 px-6">Task ID</th>
                  <th className="py-3 px-6">Repository</th>
                  <th className="py-3 px-6">Issue</th>
                  <th className="py-3 px-6">Status</th>
                  <th className="py-3 px-6">Attempts</th>
                  <th className="py-3 px-6">Created</th>
                  <th className="py-3 px-6 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {filteredTasks.map((task) => {
                  const createdAt = new Date(task.createdAt).toLocaleString([], {
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  });

                  return (
                    <tr
                      key={task.id}
                      className="hover:bg-slate-800/40 transition-colors group"
                    >
                      <td className="py-3.5 px-6 font-mono text-slate-300 font-medium">
                        <Link
                          href={`/tasks/${task.id}`}
                          className="text-sky-400 hover:underline"
                        >
                          {task.id.slice(0, 14)}...
                        </Link>
                      </td>
                      <td className="py-3.5 px-6 font-mono text-slate-300">
                        {task.repoFullName}
                      </td>
                      <td className="py-3.5 px-6">
                        <div className="font-medium text-slate-200 line-clamp-1 max-w-xs">
                          #{task.issue.number} {task.issue.title || "Untitled Issue"}
                        </div>
                      </td>
                      <td className="py-3.5 px-6">
                        <StatusBadge status={task.status} size="sm" />
                      </td>
                      <td className="py-3.5 px-6 font-mono text-slate-400">
                        {task.attempts.used} / {task.attempts.max}
                      </td>
                      <td className="py-3.5 px-6 text-slate-400 whitespace-nowrap">
                        {createdAt}
                      </td>
                      <td className="py-3.5 px-6 text-right whitespace-nowrap">
                        <Link
                          href={`/tasks/${task.id}`}
                          className="inline-flex items-center gap-1 text-sky-400 hover:text-sky-300 font-semibold"
                        >
                          <span>Open</span>
                          <ArrowRight className="w-3 h-3 group-hover:translate-x-0.5 transition-transform" />
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
