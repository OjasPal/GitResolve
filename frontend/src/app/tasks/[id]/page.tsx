"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ensureDevAuthenticated,
  getArtifact,
  getTask,
  listArtifacts,
  listEvents,
} from "@/lib/api";
import {
  ArtifactSummary,
  ArtifactView,
  EventView,
  TaskResponse,
} from "@/types";
import { StatusBadge } from "@/components/StatusBadge";
import { StageStepper } from "@/components/StageStepper";
import { ActionButtons } from "@/components/ActionButtons";
import { DiffViewer } from "@/components/DiffViewer";
import { EventTimeline } from "@/components/EventTimeline";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Clock,
  Cpu,
  ExternalLink,
  FileCode,
  FileDiff,
  FileText,
  GitBranch,
  GitPullRequest,
  Hash,
  Layers,
  ListOrdered,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Terminal,
  TestTube2,
  Timer,
} from "lucide-react";

function TaskDetailsContent() {
  const routeParams = useParams();
  const taskId = (routeParams?.id as string) || "";

  const [task, setTask] = useState<TaskResponse | null>(null);
  const [events, setEvents] = useState<EventView[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([]);
  const [activeTab, setActiveTab] = useState<"plan" | "diff" | "tests" | "events">("plan");

  const [loadedDiffHash, setLoadedDiffHash] = useState<string | null>(null);
  const [diffContent, setDiffContent] = useState<string>("");
  const [testContent, setTestContent] = useState<string>("");
  const [loadingContent, setLoadingContent] = useState<boolean>(false);

  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [isPolling, setIsPolling] = useState<boolean>(false);

  const nextAfterRef = useRef<number>(0);
  const pollingRef = useRef<NodeJS.Timeout | null>(null);

  // 1. Initial Load of Task & Events
  const fetchTaskDetails = useCallback(async (isInitial = false) => {
    try {
      if (isInitial) setLoading(true);
      setError(null);
      await ensureDevAuthenticated();
      const taskData = await getTask(taskId);
      setTask(taskData);

      // Auto-switch tabs based on lifecycle state
      if (isInitial && (taskData.status === "awaiting_approval" || taskData.status === "completed")) {
        setActiveTab("diff");
      } else if (isInitial && taskData.status === "running") {
        setActiveTab("events");
      }

      // Fetch artifacts summary list
      const artList = await listArtifacts(taskId);
      setArtifacts(artList.items || []);

      // If diff is ready, fetch diff content
      const diffArt = artList.items.find((a) => a.id === "final_diff" || a.type === "final_diff");
      if (diffArt) {
        getArtifact(taskId, diffArt.id)
          .then((v) => {
            setDiffContent(v.content);
            setLoadedDiffHash(v.truncated ? null : `sha256:${v.sha256}`);
          })
          .catch(() => {});
      }

      // If test results are ready, fetch test content
      const testArt = artList.items.find((a) => a.id === "final_tests" || a.type === "final_tests");
      if (testArt) {
        getArtifact(taskId, testArt.id)
          .then((v) => setTestContent(v.content))
          .catch(() => {});
      }

      return taskData;
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load task details");
      return null;
    } finally {
      if (isInitial) setLoading(false);
    }
  }, [taskId]);

  // 2. Incremental Event Stream Polling
  const pollEvents = useCallback(async () => {
    try {
      const res = await listEvents(taskId, nextAfterRef.current, 50);
      if (res.items && res.items.length > 0) {
        setEvents((prev) => {
          const existingSeqs = new Set(prev.map((e) => e.seq));
          const newItems = res.items.filter((e) => !existingSeqs.has(e.seq));
          return [...prev, ...newItems].sort((a, b) => a.seq - b.seq);
        });
        nextAfterRef.current = res.nextAfter;
      }
    } catch {
      // transient network error
    }
  }, [taskId]);

  // Setup initial load and dynamic polling loop
  useEffect(() => {
    let mounted = true;

    const start = async () => {
      const t = await fetchTaskDetails(true);
      await pollEvents();

      if (!t || !mounted) return;

      // Determine if task needs active polling
      const isWorking = ["queued", "planning", "running", "creating_pr"].includes(t.status);
      setIsPolling(isWorking);

      if (isWorking) {
        pollingRef.current = setInterval(async () => {
          if (!mounted) return;
          const updated = await fetchTaskDetails(false);
          await pollEvents();
          if (updated && !["queued", "planning", "running", "creating_pr"].includes(updated.status)) {
            // Task transitioned into gate or terminal state, stop fast polling
            if (pollingRef.current) clearInterval(pollingRef.current);
            setIsPolling(false);
          }
        }, 2500);
      }
    };

    start();

    return () => {
      mounted = false;
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, [taskId, task?.status, fetchTaskDetails, pollEvents]);

  // Tab switch loader
  const handleTabChange = async (tab: "plan" | "diff" | "tests" | "events") => {
    setActiveTab(tab);
    if (tab === "diff" && !diffContent) {
      const diffArt = artifacts.find((a) => a.id === "final_diff" || a.type === "final_diff");
      if (diffArt) {
        setLoadingContent(true);
        try {
          const v = await getArtifact(taskId, diffArt.id);
          setDiffContent(v.content);
          setLoadedDiffHash(v.truncated ? null : `sha256:${v.sha256}`);
        } catch {}
        setLoadingContent(false);
      }
    } else if (tab === "tests" && !testContent) {
      const testArt = artifacts.find((a) => a.id === "final_tests" || a.type === "final_tests");
      if (testArt) {
        setLoadingContent(true);
        try {
          const v = await getArtifact(taskId, testArt.id);
          setTestContent(v.content);
        } catch {}
        setLoadingContent(false);
      }
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] text-slate-400">
        <Loader2 className="w-8 h-8 text-sky-400 animate-spin mb-3" />
        <p className="text-sm font-medium">Loading repair task state...</p>
      </div>
    );
  }

  if (error || !task) {
    return (
      <div className="max-w-2xl mx-auto p-6 rounded-2xl bg-rose-950/30 border border-rose-800 text-rose-300 text-center space-y-4 my-8">
        <AlertCircle className="w-10 h-10 mx-auto text-rose-400" />
        <h2 className="text-lg font-bold text-white">Task Unavailable</h2>
        <p className="text-sm text-slate-400">{error || "Task not found."}</p>
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-white text-xs font-semibold"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Return to Dashboard</span>
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6 animate-fadeIn">
      {/* Top Breadcrumb & Actions Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-2 border-b border-slate-800">
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <Link href="/" className="hover:text-slate-200">
              Tasks
            </Link>
            <span>/</span>
            <span className="font-mono text-slate-300">{task.repoFullName}</span>
            <span>/</span>
            <span className="text-sky-400">#{task.issue.number}</span>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-xl sm:text-2xl font-bold text-white tracking-tight">
              #{task.issue.number} {task.issue.title || "Untitled Issue"}
            </h1>
            <StatusBadge status={task.status} size="md" />
          </div>
        </div>

        {/* Dynamic Action Buttons (Gate 1, Gate 2, Cancel, Run Again) */}
        <div className="shrink-0">
          <ActionButtons
            task={task}
            reviewedDiffHash={activeTab === "diff" ? loadedDiffHash : null}
            onActionSuccess={(updated) => {
              setTask(updated);
              fetchTaskDetails(false);
            }}
          />
        </div>
      </div>

      {/* Stage Tracker Stepper */}
      <div className="p-4 sm:p-5 rounded-2xl bg-slate-900/60 border border-slate-800 shadow-lg">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
            Execution Stage Pipeline
          </span>
          <span className="text-xs font-mono text-sky-400">
            Stage: {task.stage.toUpperCase()}
          </span>
        </div>
        <StageStepper currentStage={task.stage} status={task.status} />
      </div>

      {/* Completed Success Banner (PR opened) */}
      {task.status === "completed" && task.pr && (
        <div className="p-5 rounded-2xl bg-gradient-to-r from-emerald-950/70 via-slate-900 to-emerald-950/70 border border-emerald-600/80 shadow-xl shadow-emerald-950/40 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div className="flex items-center gap-3.5">
            <div className="w-10 h-10 rounded-xl bg-emerald-500/20 border border-emerald-400/40 flex items-center justify-center text-emerald-400 shrink-0">
              <GitPullRequest className="w-5 h-5 stroke-[2.5]" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-emerald-200">
                Pull Request Result
              </h3>
              <p className="text-xs text-slate-300 mt-0.5">
                Branch: <span className="font-mono text-emerald-400">{task.pr.branch}</span> · All sandbox tests verified.
              </p>
            </div>
          </div>
          <a
            href={task.pr.url}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold text-xs shadow-md shadow-emerald-500/30 transition-all shrink-0 cursor-pointer"
          >
            <span>View Pull Request #{task.pr.number}</span>
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
        </div>
      )}

      {/* Failure Notification Box */}
      {task.failure && (
        <div className="p-5 rounded-2xl bg-rose-950/40 border border-rose-800 text-rose-200 space-y-2">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-5 h-5 text-rose-400 shrink-0" />
            <h3 className="text-sm font-bold text-white uppercase tracking-wider">
              Investigation Failure: {task.failure.code}
            </h3>
          </div>
          <p className="text-xs text-rose-300 font-mono leading-relaxed pl-7">
            {task.failure.message}
          </p>
        </div>
      )}

      {/* Gate 1 Attention Callout */}
      {task.status === "awaiting_start" && (
        <div className="p-4 rounded-xl bg-purple-950/40 border border-purple-600/60 shadow-lg text-purple-200 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div>
            <p className="text-sm font-bold text-purple-200">
              Human Gate 1: Investigation Plan Ready
            </p>
            <p className="text-xs text-purple-300/80 mt-0.5">
              Review the proposed investigation steps below. Repair execution still requires the Agent Lead LangGraph implementation.
            </p>
          </div>
        </div>
      )}

      {/* Gate 2 Attention Callout */}
      {task.status === "awaiting_approval" && (
        <div className="p-4 rounded-xl bg-emerald-950/40 border border-emerald-600/60 shadow-lg text-emerald-200 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div>
            <p className="text-sm font-bold text-emerald-200">
              Human Gate 2: Code Diff & Test Verification Ready
            </p>
            <p className="text-xs text-emerald-300/80 mt-0.5">
              Inspect the complete proposed diff and test evidence before approving. Local fixture results do not represent real repository repairs.
            </p>
          </div>
        </div>
      )}

      {/* Metadata & Budgets Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
        {/* Attempts */}
        <div className="p-3.5 rounded-xl bg-slate-900/50 border border-slate-800 flex items-center gap-3">
          <TestTube2 className="w-4 h-4 text-sky-400 shrink-0" />
          <div>
            <span className="text-[11px] text-slate-500 block">Repair Attempts</span>
            <span className="font-mono font-bold text-slate-200">
              {task.attempts.used} / {task.attempts.max} used
            </span>
          </div>
        </div>

        {/* LLM Model Calls */}
        <div className="p-3.5 rounded-xl bg-slate-900/50 border border-slate-800 flex items-center gap-3">
          <Cpu className="w-4 h-4 text-purple-400 shrink-0" />
          <div>
            <span className="text-[11px] text-slate-500 block">Model Calls</span>
            <span className="font-mono font-bold text-slate-200">
              {task.budgets.modelCalls} / {task.limits.maxModelCalls} calls
            </span>
          </div>
        </div>

        {/* Execution Time */}
        <div className="p-3.5 rounded-xl bg-slate-900/50 border border-slate-800 flex items-center gap-3">
          <Timer className="w-4 h-4 text-amber-400 shrink-0" />
          <div>
            <span className="text-[11px] text-slate-500 block">Execution Time</span>
            <span className="font-mono font-bold text-slate-200">
              {task.budgets.execSeconds ? `${task.budgets.execSeconds.toFixed(1)}s` : "0.0s"}
            </span>
          </div>
        </div>

        {/* Diff Hash / Commit */}
        <div className="p-3.5 rounded-xl bg-slate-900/50 border border-slate-800 flex items-center gap-3">
          <Hash className="w-4 h-4 text-emerald-400 shrink-0" />
          <div className="min-w-0">
            <span className="text-[11px] text-slate-500 block">Diff Verification Hash</span>
            <span className="font-mono text-[11px] text-slate-300 truncate block">
              {task.finalDiffHash ? task.finalDiffHash.slice(0, 18) + "..." : "Pending"}
            </span>
          </div>
        </div>
      </div>

      {/* Tabs Navigation */}
      <div className="border-b border-slate-800 flex items-center gap-2">
        <button
          onClick={() => handleTabChange("plan")}
          className={`flex items-center gap-2 px-4 py-2.5 text-xs font-semibold border-b-2 transition-all cursor-pointer ${
            activeTab === "plan"
              ? "border-sky-500 text-sky-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <FileText className="w-4 h-4" />
          <span>Investigation Plan</span>
        </button>

        <button
          onClick={() => handleTabChange("diff")}
          className={`flex items-center gap-2 px-4 py-2.5 text-xs font-semibold border-b-2 transition-all cursor-pointer ${
            activeTab === "diff"
              ? "border-emerald-500 text-emerald-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <FileDiff className="w-4 h-4" />
          <span>Proposed Code Diff</span>
          {task.status === "awaiting_approval" && (
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping" />
          )}
        </button>

        <button
          onClick={() => handleTabChange("tests")}
          className={`flex items-center gap-2 px-4 py-2.5 text-xs font-semibold border-b-2 transition-all cursor-pointer ${
            activeTab === "tests"
              ? "border-amber-500 text-amber-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <TestTube2 className="w-4 h-4" />
          <span>Sandbox Test Results</span>
        </button>

        <button
          onClick={() => handleTabChange("events")}
          className={`flex items-center gap-2 px-4 py-2.5 text-xs font-semibold border-b-2 transition-all cursor-pointer ${
            activeTab === "events"
              ? "border-purple-500 text-purple-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <Terminal className="w-4 h-4" />
          <span>Live Timeline & Logs</span>
          {isPolling && (
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping" />
          )}
        </button>
      </div>

      {/* Tab 1: Investigation Plan Content */}
      {activeTab === "plan" && (
        <div className="space-y-4">
          {task.plan ? (
            <div className="p-6 rounded-2xl bg-slate-900/60 border border-slate-800 space-y-6">
              {/* Problem Summary */}
              <div className="space-y-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                  AI Problem Summary
                </span>
                <p className="text-sm text-slate-200 leading-relaxed p-4 rounded-xl bg-slate-950 border border-slate-800/80">
                  {task.plan.problemSummary}
                </p>
              </div>

              {/* Steps List */}
              <div className="space-y-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                  Investigation & Repair Steps
                </span>
                <ol className="space-y-2.5">
                  {task.plan.steps.map((step, idx) => (
                    <li
                      key={idx}
                      className="flex items-start gap-3 p-3 rounded-xl bg-slate-950/80 border border-slate-800/80 text-xs text-slate-300"
                    >
                      <span className="flex items-center justify-center w-5 h-5 rounded-full bg-sky-500/20 text-sky-400 font-bold text-[11px] shrink-0 mt-0.5">
                        {idx + 1}
                      </span>
                      <span className="leading-relaxed">{step}</span>
                    </li>
                  ))}
                </ol>
              </div>

              {/* Likely Files */}
              {task.plan.likelyFiles && task.plan.likelyFiles.length > 0 && (
                <div className="space-y-2">
                  <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                    Likely Affected Files
                  </span>
                  <div className="flex flex-wrap gap-2">
                    {task.plan.likelyFiles.map((f, i) => (
                      <span
                        key={i}
                        className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg bg-slate-950 border border-slate-800 text-sky-300 font-mono text-xs"
                      >
                        <FileCode className="w-3.5 h-3.5 text-sky-400" />
                        <span>{f}</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="p-8 text-center text-slate-500 bg-slate-900/40 rounded-xl border border-slate-800">
              <Clock className="w-6 h-6 mx-auto mb-2 text-slate-600 animate-spin" />
              <p className="text-sm font-medium">Investigation plan is currently being generated by the LangGraph agent...</p>
            </div>
          )}
        </div>
      )}

      {/* Tab 2: Proposed Diff Content */}
      {activeTab === "diff" && (
        <div className="space-y-4">
          {loadingContent ? (
            <div className="p-8 text-center text-slate-500 bg-slate-900/40 rounded-xl border border-slate-800">
              <Loader2 className="w-6 h-6 mx-auto mb-2 text-sky-400 animate-spin" />
              <p className="text-sm">Fetching proposed diff artifact...</p>
            </div>
          ) : (
            <DiffViewer diffText={diffContent} filename={`${task.repoFullName} proposed patch`} />
          )}
        </div>
      )}

      {/* Tab 3: Sandbox Test Results Content */}
      {activeTab === "tests" && (
        <div className="space-y-4">
          {loadingContent ? (
            <div className="p-8 text-center text-slate-500 bg-slate-900/40 rounded-xl border border-slate-800">
              <Loader2 className="w-6 h-6 mx-auto mb-2 text-amber-400 animate-spin" />
              <p className="text-sm">Loading test execution logs...</p>
            </div>
          ) : testContent ? (
            <div className="rounded-xl border border-slate-800 bg-slate-950 p-4 font-mono text-xs overflow-x-auto text-slate-300 whitespace-pre">
              {testContent}
            </div>
          ) : (
            <div className="p-8 text-center text-slate-500 bg-slate-900/40 rounded-xl border border-slate-800">
              <TestTube2 className="w-6 h-6 mx-auto mb-2 text-slate-600" />
              <p className="text-sm">No test logs available yet. Tests run during the execution phase in the Docker sandbox.</p>
            </div>
          )}
        </div>
      )}

      {/* Tab 4: Live Events Timeline Stream */}
      {activeTab === "events" && (
        <div className="min-h-[400px]">
          <EventTimeline events={events} isPolling={isPolling} />
        </div>
      )}
    </div>
  );
}

export default function TaskDetailsPage() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-col items-center justify-center min-h-[60vh] text-slate-400">
          <Loader2 className="w-8 h-8 text-sky-400 animate-spin mb-3" />
          <p className="text-sm font-medium">Loading repair task state...</p>
        </div>
      }
    >
      <TaskDetailsContent />
    </Suspense>
  );
}
