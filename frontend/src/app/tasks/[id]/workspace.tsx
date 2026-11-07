"use client";

import { useQuery } from "@tanstack/react-query";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import {
  checkBackendHealth,
  getArtifact,
  getTask,
  listArtifacts,
  listEvents} from "@/lib/api";
import {
  EventView,
  TaskResponse} from "@/types";
import { GateConversation } from "@/components/GateConversation";
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
  GitPullRequest,
  HelpCircle,
  ListOrdered,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Terminal,
  TestTube2,
  XCircle} from "lucide-react";

const SAFE_GUIDANCE_URLS = new Set([
  "https://console.groq.com/keys",
  "https://aistudio.google.com/apikey",
  "https://platform.openai.com/api-keys",
  "https://console.anthropic.com/settings/keys",
  "https://help.shopify.com/en/manual/Fulfillment/setup/notifications/webhooks",
  "https://dashboard.stripe.com/webhooks",
]);

function renderTextWithSafeLinks(text: string) {
  const parts = text.split(/(https:\/\/[^\s)]+)/g);
  return parts.map((part, idx) =>
    SAFE_GUIDANCE_URLS.has(part) ? (
      <a
        key={idx}
        href={part}
        target="_blank"
        rel="noopener noreferrer"
        className="underline underline-offset-2 font-medium"
      >
        {part}
      </a>
    ) : (
      <span key={idx}>{part}</span>
    )
  );
}

function TaskWorkspaceContent() {
  const routeParams = useParams();
  const taskId = (routeParams?.id as string) || "";

  const [workerStatus, setWorkerStatus] = useState<string | null>(null);
  const [task, setTask] = useState<TaskResponse | null>(null);
  const [planEditing, setPlanEditing] = useState(false);
  const [events, setEvents] = useState<EventView[]>([]);
  const [activeTab, setActiveTab] = useState<"investigation" | "plan" | "diff" | "tests" | "pr" | "events">("investigation");

  // Content states for diff and test artifacts
  const [loadedDiffHash, setLoadedDiffHash] = useState<string | null>(null);
  const [diffContent, setDiffContent] = useState<string>("");
  const [testContent, setTestContent] = useState<string>("");

  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const isPolling = Boolean(task && (task.gateChatBusy || ["queued", "planning", "running", "creating_pr"].includes(task.status)));

  const nextAfterRef = useRef<number>(0);

  const lastLifecycleRef = useRef<{ taskId: string; status: string } | null>(null);

  // Load Task & Artifacts
  const fetchTaskDetails = useCallback(async (isInitial = false) => {
    try {
      if (isInitial) setLoading(true);
      setError(null);

      const taskData = await getTask(taskId);
      const health = await checkBackendHealth();
      setWorkerStatus(health.worker?.status || null);
      setTask(taskData);

      // Follow a newly reached human gate once, without repeatedly overriding tab choices.
      const previous = lastLifecycleRef.current;
      if (taskData.status === "awaiting_approval" &&
          (!previous || previous.taskId !== taskId || previous.status !== "awaiting_approval")) {
        setActiveTab("diff");
      }
      lastLifecycleRef.current = { taskId, status: taskData.status };

      // Auto-switch tabs to relevant section based on lifecycle state on initial load
      if (isInitial) {
        if (taskData.status === "awaiting_approval") {
          setActiveTab("diff");
        } else if (taskData.status === "resolved_with_guidance") {
          setActiveTab("investigation");
        } else if (taskData.status === "completed" && taskData.pr) {
          setActiveTab("pr");
        } else if (taskData.status === "awaiting_start") {
          setActiveTab("plan");
        } else if (taskData.status === "running") {
          setActiveTab("tests");
        }
      }

      // Fetch artifacts summary
      const artList = await listArtifacts(taskId);


      // If diff artifact exists, fetch content
      const diffArt = artList.items.find((a) => a.id === "final_diff" || a.type === "final_diff");
      setLoadedDiffHash(null);
      if (diffArt) {
        await getArtifact(taskId, diffArt.id)
          .then((v) => {
            setDiffContent(v.content);
            setLoadedDiffHash(v.truncated ? null : `sha256:${v.sha256}`);
          })
          .catch((err) => setError(err instanceof Error ? err.message : "Unable to load artifact."));
      }

      // If test artifact exists, fetch content
      const testArt = artList.items.find((a) => a.id === "final_tests" || a.type === "final_tests");
      if (testArt) {
        await getArtifact(taskId, testArt.id)
          .then((v) => setTestContent(v.content))
          .catch((err) => setError(err instanceof Error ? err.message : "Unable to load artifact."));
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load task details");
    } finally {
      if (isInitial) setLoading(false);
    }
  }, [taskId]);

  // Incremental event polling
  const fetchNewEvents = useCallback(async () => {
    try {
      // Drain pages even for terminal tasks, where no future poll will run.
      let hasMore = true;
      while (hasMore) {
        const after = nextAfterRef.current;
        const res = await listEvents(taskId, after, 50);
        if (res.items.length) {
          setEvents((prev) => {
            const existing = new Set(prev.map((event) => event.seq));
            return [...prev, ...res.items.filter((event) => !existing.has(event.seq))].sort((a, b) => a.seq - b.seq);
          });
        }
        nextAfterRef.current = res.nextAfter;
        hasMore = res.items.length === 50 && res.nextAfter > after;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load task events.");
    }
  }, [taskId]);

  useEffect(() => {
    nextAfterRef.current = 0;
    const timer = setTimeout(async () => {
      setEvents([]);
      setLoadedDiffHash(null);
      setDiffContent("");
      setTestContent("");
      await fetchTaskDetails(true);
      await fetchNewEvents();
    }, 0);
    return () => clearTimeout(timer);
  }, [fetchTaskDetails, fetchNewEvents]);

  useQuery({
    queryKey: ["repair-timeline", taskId],
    queryFn: async () => { await fetchTaskDetails(false); await fetchNewEvents(); return true; },
    enabled: Boolean(taskId) && isPolling,
    refetchInterval: 3000,
    refetchIntervalInBackground: false,
  });

  if (loading && !task) {
    return (
      <div className="py-24 flex flex-col items-center justify-center gap-3 text-neutral-500">
        <Loader2 className="w-8 h-8 animate-spin text-neutral-400" />
        <p className="text-sm font-mono">Loading task workspace...</p>
      </div>
    );
  }

  if (error && !task) {
    return (
      <div className="max-w-xl mx-auto py-16 space-y-4 text-center">
        <div className="w-12 h-12 rounded-xl border border-red-500/30 bg-red-500/10 text-red-500 flex items-center justify-center mx-auto">
          <AlertCircle className="w-6 h-6" />
        </div>
        <div className="space-y-1">
          <h2 className="text-lg font-bold text-neutral-900 dark:text-neutral-100">
            Task Not Found or Backend Offline
          </h2>
          <p className="text-xs text-neutral-500">{error}</p>
        </div>
        <div className="pt-2 flex items-center justify-center gap-3">
          <Link
            href="/dashboard"
            className="px-4 py-2 rounded-lg border border-neutral-300 dark:border-neutral-700 text-xs font-medium"
          >
            Return to Dashboard
          </Link>
          <button
            onClick={() => fetchTaskDetails(true)}
            className="px-4 py-2 rounded-lg bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 text-xs font-semibold"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!task) return null;

  return (
    <div className="space-y-6 max-w-6xl mx-auto">
      {error && <p role="alert" className="border border-red-500/30 p-3 rounded-lg text-red-500 text-sm">{error}</p>}
      {task.status === "queued" && workerStatus && ["disabled", "stopped"].includes(workerStatus) && <p role="alert" className="p-4 border border-amber-500/40 rounded-lg text-amber-600 dark:text-amber-400 text-sm">The API is online, but its local worker is not running. This task cannot start until the backend worker is enabled.</p>}
      {/* Top Breadcrumb & Live Status Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs border-b border-neutral-200 dark:border-neutral-800 pb-3">
        <div className="flex items-center gap-2 text-neutral-500 truncate">
          <Link href="/dashboard" className="hover:text-neutral-900 dark:hover:text-neutral-100 flex items-center gap-1">
            <ArrowLeft className="w-3.5 h-3.5" />
            <span>Dashboard</span>
          </Link>
          <span>/</span>
          <Link href="/tasks" className="hover:text-neutral-900 dark:hover:text-neutral-100">
            Tasks
          </Link>
          <span>/</span>
          <span className="font-mono text-neutral-900 dark:text-neutral-100 truncate">
            {task.repoFullName} {task.issue.number ? `#${task.issue.number}` : "Direct report"}
          </span>
        </div>

        <div className="flex items-center gap-3 shrink-0">
          {isPolling && (
            <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border border-neutral-500/30 bg-neutral-500/10 text-neutral-600 dark:text-neutral-400 font-mono text-[11px]">
              <span className="w-1.5 h-1.5 rounded-full bg-neutral-500 animate-ping" />
              <span>{task.status === "queued" ? "Waiting for worker" : task.status === "planning" ? "Agent planning" : task.status === "creating_pr" ? "Creating pull request" : "Agent working"}</span>
            </div>
          )}
          <button
            onClick={() => {
              fetchTaskDetails(false);
              fetchNewEvents();
            }}
            className="p-1.5 rounded-md border border-neutral-300 dark:border-neutral-700 hover:bg-neutral-100 dark:hover:bg-neutral-800 text-neutral-600 dark:text-neutral-300 cursor-pointer"
            title="Refresh state"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Task Overview Header */}
      <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-5 bg-white dark:bg-neutral-900 shadow-sm space-y-4">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="space-y-1.5 min-w-0">
            <div className="flex flex-wrap items-center gap-2.5">
              <span className="font-mono text-xs px-2 py-0.5 rounded border border-neutral-200 dark:border-neutral-800 bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-300">
                {task.repoFullName} {task.issue.number ? `#${task.issue.number}` : "Direct report"}
              </span>
              {task.failure?.code === "llm_unavailable" ? <span className="status-chip" data-tone="warn">{task.stage === "evaluate" ? "Evaluation incomplete" : "Investigation paused"}</span> : <StatusBadge status={task.status} />}
            </div>
            <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-neutral-900 dark:text-neutral-100 truncate">
              {task.issue.number ? task.issue.title || `Issue #${task.issue.number}` : task.plan ? (task.plan.title || task.plan.problemSummary || task.issue.title || "Repair request").split(/[.!?\n]/)[0].slice(0, 100) : "Analyzing your request"}
            </h1>
          </div>

          {/* Action buttons (Human gates: Start, Approve, Reject, Cancel) */}
          <div className="shrink-0 task-actions">
            <ActionButtons
              task={task}
              planEditing={planEditing || Boolean(task.gateChatBusy)}
              diffText={loadedDiffHash === task.finalDiffHash ? diffContent : undefined}
              reviewedDiffHash={activeTab === "diff" ? loadedDiffHash : null}
              onReviewDiff={() => setActiveTab("diff")}
              onActionSuccess={(updated) => setTask(updated)}
            />
          </div>
        </div>

        {/* External-Action Guidance Card (Terminal Non-Failure) */}
        {task.status === "resolved_with_guidance" && (
          <div className="p-4 rounded-xl border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-950 text-xs space-y-3" role="status">
            <div className="flex items-start gap-2.5">
              <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5 text-neutral-700 dark:text-neutral-200" />
              <div className="space-y-1 flex-1">
                <p className="font-semibold text-sm text-neutral-900 dark:text-neutral-100">
                  {typeof task.result?.title === "string" && task.result.title
                    ? task.result.title
                    : "External setup required"}
                </p>
                <p className="text-neutral-700 dark:text-neutral-300 leading-relaxed">
                  {typeof task.result?.summary === "string" && task.result.summary
                    ? task.result.summary
                    : typeof task.result?.explanation === "string" && task.result.explanation
                    ? task.result.explanation
                    : "We checked your repository. No code change is needed — complete the external configuration steps below."}
                </p>
              </div>
            </div>

            {Array.isArray(task.result?.whatWeFound) && task.result.whatWeFound.length > 0 && (
              <div className="space-y-1.5 pt-2 border-t border-neutral-200 dark:border-neutral-800">
                <p className="font-semibold text-neutral-800 dark:text-neutral-200">
                  What we found in your repository
                </p>
                <ul className="space-y-1 font-mono text-[11px] text-neutral-600 dark:text-neutral-400">
                  {(task.result.whatWeFound as Array<string | { path?: string; line?: number; detail?: string }>).map((item, idx) => (
                    <li key={idx} className="flex items-center gap-1.5">
                      <FileCode className="w-3.5 h-3.5 shrink-0 text-neutral-400" />
                      {typeof item === "string" ? (
                        <span className="text-neutral-900 dark:text-neutral-100">{item}</span>
                      ) : (
                        <>
                          <span className="text-neutral-900 dark:text-neutral-100">
                            {item.path}{item.line ? `:${item.line}` : ""}
                          </span>
                          {item.detail && <span>— {item.detail}</span>}
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {Array.isArray(task.result?.steps) && task.result.steps.length > 0 && (
              <div className="space-y-1.5 pt-2 border-t border-neutral-200 dark:border-neutral-800">
                <p className="font-semibold text-neutral-800 dark:text-neutral-200">
                  Steps to complete setup
                </p>
                <ol className="list-decimal list-inside space-y-1.5 text-neutral-800 dark:text-neutral-200 leading-relaxed">
                  {(task.result.steps as Array<string | { text?: string; link?: string }>).map((step, idx) => {
                    const stepText = typeof step === "string" ? step : String(step?.text || "");
                    const stepLink = typeof step === "object" && step !== null && typeof step.link === "string" && SAFE_GUIDANCE_URLS.has(step.link) ? step.link : null;
                    return (
                      <li key={idx}>
                        {renderTextWithSafeLinks(stepText)}
                        {stepLink && (
                          <>
                            {" "}
                            <a
                              href={stepLink}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="underline underline-offset-2 font-medium inline-flex items-center gap-1"
                            >
                              <span>{stepLink}</span>
                              <ExternalLink className="w-3 h-3 inline" />
                            </a>
                          </>
                        )}
                      </li>
                    );
                  })}
                </ol>
              </div>
            )}

            {typeof task.result?.afterYouDoThis === "string" && task.result.afterYouDoThis && (
              <p className="text-neutral-600 dark:text-neutral-400 pt-1">
                <strong>After you do this:</strong> {task.result.afterYouDoThis}
              </p>
            )}

            <p className="text-[11px] text-neutral-500 pt-2 border-t border-neutral-200 dark:border-neutral-800">
              {typeof task.result?.securityNote === "string" && task.result.securityNote
                ? task.result.securityNote
                : "Never commit secrets or .env to git. GitResolve never asks you to paste secret values into the app."}
            </p>
          </div>
        )}

        {/* Mixed Resolution Setup Note if applicable */}
        {(task.finalDiffSummary?.setupNote || (typeof task.verification?.setupNote === "string" && task.verification.setupNote)) && (
          <div className="p-3.5 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-950 text-xs space-y-2" role="note">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 shrink-0 text-neutral-700 dark:text-neutral-200" />
              <p className="font-semibold text-neutral-900 dark:text-neutral-100">
                Setup you must do outside the codebase
              </p>
            </div>
            {Array.isArray(task.finalDiffSummary?.whatWeFound) && (task.finalDiffSummary.whatWeFound as string[]).length > 0 && (
              <div className="font-mono text-[11px] text-neutral-600 dark:text-neutral-400 space-y-0.5">
                {(task.finalDiffSummary.whatWeFound as string[]).map((item, i) => (
                  <div key={i} className="flex items-center gap-1.5">
                    <FileCode className="w-3.5 h-3.5 shrink-0 text-neutral-400" />
                    <span className="text-neutral-900 dark:text-neutral-100">{item}</span>
                  </div>
                ))}
              </div>
            )}
            <p className="text-neutral-700 dark:text-neutral-300 whitespace-pre-wrap leading-relaxed">
              {renderTextWithSafeLinks(String(task.finalDiffSummary?.setupNote || task.verification?.setupNote))}
            </p>
          </div>
        )}

        {/* Task Failure banner if applicable */}
        {task.failure && task.status !== "resolved_with_guidance" && (
          <div className="p-3.5 rounded-lg border border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400 text-xs flex items-start gap-2.5">
            <XCircle className="w-4 h-4 shrink-0 mt-0.5" />
            <div className="space-y-0.5">
              <p className="font-semibold">
                {task.failure.code === "llm_unavailable" ? (task.stage === "evaluate" ? "Evaluation incomplete" : "Investigation temporarily paused") : task.failure.code === "timeout" ? "This repair needs more time" : task.failure.code === "no_fix_found" ? "This repair needs another look" : task.failure.code === "sandbox_error" ? "The test environment needs attention" : "This repair couldn’t finish"}
              </p>
              <p className="text-sm">{task.failure.code === "llm_unavailable" ? "The model service couldn’t complete this step. Your progress and evidence are saved below. No pull request will be created without the required checks. Please try again once the service is available." : task.failure.code === "timeout" ? "The run reached its time limit. Your progress is saved below; try a smaller change or start another run." : task.failure.code === "no_fix_found" ? "The agent tried several approaches but hasn’t verified a safe fix. Review the evidence below and try a more focused request." : "We couldn’t complete verification. Your repository is unchanged. Review the details or try again."}</p>
            </div>
          </div>
        )}

        {/* Quick stats grid */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2 border-t border-neutral-100 dark:border-neutral-800 font-mono text-xs text-neutral-500">
          <div>
            <span className="text-neutral-400">Repair Attempts:</span>{" "}
            <span className="text-neutral-800 dark:text-neutral-200 font-semibold">
              {task.attempts.used} / {task.attempts.max}
            </span>
          </div>
          <div>
            <span className="text-neutral-400">Model Calls:</span>{" "}
            <span className="text-neutral-800 dark:text-neutral-200 font-semibold">
              {task.budgets.modelCalls ?? 0} / {task.limits.maxModelCalls ?? '∞'}
            </span>
          </div>
          <div>
            <span className="text-neutral-400">Sandbox Time:</span>{" "}
            <span className="text-neutral-800 dark:text-neutral-200 font-semibold">
              {(task.budgets.execSeconds ?? 0).toFixed(1)}s
            </span>
          </div>
          <div>
            <span className="text-neutral-400">Tokens In/Out:</span>{" "}
            <span className="text-neutral-800 dark:text-neutral-200 font-semibold">
              {task.budgets.inputTokens ?? 0}/{task.budgets.outputTokens ?? 0}
            </span>
          </div>
        </div>
      </div>

      <GateConversation key={task.id + task.status + (task.gateRevision||0)} task={task} onSave={setTask} onEditingChange={setPlanEditing}/>
      <div className="task-split"><aside className="task-live-rail glass-card"><span className="eyebrow">Repair progress</span><StageStepper currentStage={task.stage} status={task.status}/><div className="mt-8 border-t border-neutral-800 pt-6"><EventTimeline events={events} isPolling={isPolling}/></div></aside><section className="task-tab-content"><div className="task-tabs" role="tablist" aria-label="Repair evidence">{([{id:"plan",name:"Plan"},{id:"investigation",name:"Evidence"},{id:"diff",name:"Diff"},{id:"tests",name:"Tests"},{id:"pr",name:"Pull request"},{id:"events",name:`Events (${events.length})`}] as const).map(tab=><button key={tab.id} role="tab" aria-selected={activeTab===tab.id} onClick={()=>setActiveTab(tab.id)}>{tab.name}</button>)}</div>
      {/* Tab 1: Investigation */}
      {activeTab === "investigation" && (
        <div className="space-y-5">
          {/* Issue raw description */}
          <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-5 bg-white dark:bg-neutral-900 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
                <FileText className="w-4 h-4 text-neutral-500" />
                <span>Reported Issue Context</span>
              </h3>
              {task.issue.url && (
                <a
                  href={task.issue.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100 underline flex items-center gap-1"
                >
                  <span>View on GitHub</span>
                  <ExternalLink className="w-3 h-3" />
                </a>
              )}
            </div>

            <div className="p-3.5 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-950 font-mono text-xs text-neutral-800 dark:text-neutral-200 whitespace-pre-wrap leading-relaxed max-h-60 overflow-y-auto">
              {task.issue.body || "No issue description provided in GitHub payload."}
            </div>
          </div>

          {/* Agent understanding & investigation summary */}
          {task.plan ? (
            <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-5 bg-white dark:bg-neutral-900 space-y-4">
              <h3 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
                <Cpu className="w-4 h-4 text-neutral-500" />
                <span>Agent Problem Understanding</span>
              </h3>

              <div className="p-4 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-950 text-xs text-neutral-800 dark:text-neutral-200 leading-relaxed">
                {task.plan.problemSummary}
              </div>

              {/* Likely files identified */}
              {(task.plan.likelyFiles || task.plan.likelyFiles || []).length > 0 && (
                <div className="space-y-2">
                  <h4 className="text-xs font-semibold text-neutral-700 dark:text-neutral-300">
                    Suspect Files Identified
                  </h4>
                  <div className="flex flex-wrap gap-2">
                    {(task.plan.likelyFiles || task.plan.likelyFiles || []).map((file: string, idx: number) => (
                      <span
                        key={idx}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-neutral-200 dark:border-neutral-800 bg-neutral-100 dark:bg-neutral-800 font-mono text-xs text-neutral-800 dark:text-neutral-200"
                      >
                        <FileCode className="w-3.5 h-3.5 text-neutral-400" />
                        <span>{file}</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Questions for reporter if needed */}
              {task.plan.questions && task.plan.questions.length > 0 && (
                <div className="p-4 rounded-lg border border-amber-500/30 bg-amber-500/5 text-xs text-amber-700 dark:text-amber-300 space-y-2">
                  <div className="flex items-center gap-2 font-semibold">
                    <HelpCircle className="w-4 h-4 shrink-0" />
                    <span>Questions for Issue Reporter (Missing Context)</span>
                  </div>
                  <ul className="list-disc list-inside space-y-1 text-neutral-700 dark:text-neutral-300">
                    {task.plan.questions.map((q, idx) => (
                      <li key={idx}>{q}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-8 bg-white dark:bg-neutral-900 text-center space-y-2">
              <Loader2 className="w-6 h-6 animate-spin text-neutral-400 mx-auto" />
              <p className="text-xs font-semibold text-neutral-700 dark:text-neutral-300">
                {task.status === "queued" ? "Queued — waiting for a worker to pick up this task" : task.status === "planning" ? "Agent is analyzing your request..." : task.failure ? "Analysis stopped — see the failure details above" : "No analysis available yet"}
              </p>
              <p className="text-xs text-neutral-500">
                {task.status === "queued" ? "No model call has started. The worker must be running and use the same task store as the API." : "The plan and investigation events appear here as the worker progresses."}
              </p>
            </div>
          )}
        </div>
      )}

      {/* Tab 2: Implementation Plan */}
      {activeTab === "plan" && (
        <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-5 bg-white dark:bg-neutral-900 space-y-5">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-neutral-100 dark:border-neutral-800 pb-3">
            <div>
              <h3 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
                <ListOrdered className="w-4 h-4 text-neutral-500" />
                <span>Proposed Investigation & Repair Steps</span>
              </h3>
              <p className="text-xs text-neutral-500 mt-0.5">
                Formulated by the LangGraph agent before entering the Docker execution sandbox.
              </p>
            </div>

            {/* Human Gate 1 indicator */}
            {task.status === "awaiting_start" && (
              <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400 text-xs font-semibold">
                <Clock className="w-3.5 h-3.5" />
                <span>Awaiting Your Approval to Start Repair</span>
              </div>
            )}
          </div>

          {task.plan ? (
            <div className="space-y-3">
              {task.plan.steps.map((step, idx) => (
                <div
                  key={idx}
                  className="flex items-start gap-3 p-3.5 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-950/50"
                >
                  <span className="w-6 h-6 rounded-full border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-neutral-700 dark:text-neutral-300 font-mono text-xs font-bold flex items-center justify-center shrink-0 mt-0.5">
                    {idx + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-xs text-neutral-900 dark:text-neutral-100 leading-relaxed font-medium">
                      {step}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="p-8 text-center text-xs text-neutral-500">
              {task.status === "planning"
                ? "Agent is currently formulating the investigation plan..."
                : "No plan generated yet."}
            </div>
          )}
        </div>
      )}

      {/* Tab 3: Proposed Patch / Diff */}
      {activeTab === "diff" && (
        <div className="space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-neutral-200 dark:border-neutral-800 pb-3">
            <div>
              <h3 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
                <FileDiff className="w-4 h-4 text-neutral-500" />
                <span>Proposed Changes</span>
              </h3>
              <p className="text-xs text-neutral-500 mt-0.5">
                Tests check behavior. Review the interface and design before approving UI changes.
              </p>
            </div>

            {task.finalDiffHash && (
              <span className="font-mono text-xs px-2.5 py-1 rounded border border-neutral-200 dark:border-neutral-800 bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-400">
                {task.finalDiffHash}
              </span>
            )}
          </div>

          {task.finalDiffSummary?.commitMessage && (
            <div className="rounded-xl border border-neutral-200 dark:border-neutral-800 p-4 space-y-2">
              <p className="text-xs text-neutral-500">Proposed commit and PR title</p>
              <p className="text-sm font-medium">{task.finalDiffSummary.commitMessage}</p>
              {task.finalDiffSummary.uiVerification && (
                <p className="text-xs text-neutral-500">{task.finalDiffSummary.uiVerification}</p>
              )}
            </div>
          )}

          {diffContent ? (
            <div className="space-y-4">
              <DiffViewer diffText={diffContent} />

              {/* Approval status banner */}
              {task.approval && (
                <div className="p-4 rounded-xl border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-900 text-xs flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <ShieldCheck className="w-4 h-4 text-emerald-500" />
                    <span>
                      Diff approval state:{" "}
                      <strong className="text-neutral-900 dark:text-neutral-100 uppercase font-mono">
                        {task.approval.state}
                      </strong>
                    </span>
                  </div>
                  {task.approval.at && (
                    <span className="text-neutral-500 font-mono">
                      {new Date(task.approval.at).toLocaleString()}
                    </span>
                  )}
                </div>
              )}
            </div>
          ) : (
            <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-12 bg-white dark:bg-neutral-900 text-center space-y-2">
              <FileDiff className="w-8 h-8 text-neutral-300 dark:text-neutral-700 mx-auto" />
              <h4 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
                No Patch Generated Yet
              </h4>
              <p className="text-xs text-neutral-500 max-w-md mx-auto">
                {task.status === "awaiting_start"
                  ? "Approve the plan to begin repair execution in the Docker sandbox."
                  : task.status === "running"
                  ? "The agent is currently generating candidate patches and executing tests in the sandbox..."
                  : "A patch will appear here once the agent successfully resolves the issue."}
              </p>
            </div>
          )}
        </div>
      )}

      {/* Tab 4: Sandbox & Test Results */}
      {activeTab === "tests" && (
        <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-5 bg-white dark:bg-neutral-900 space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-neutral-100 dark:border-neutral-800 pb-3">
            <div>
              <h3 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
                <TestTube2 className="w-4 h-4 text-neutral-500" />
                <span>Docker Sandbox Execution & Test Suite Output</span>
              </h3>
              <p className="text-xs text-neutral-500 mt-0.5">
                Output captured from test runner execution inside the isolated sandbox container
              </p>
            </div>

            <div className="flex items-center gap-2 font-mono text-xs">
              <span className="px-2 py-0.5 rounded border border-neutral-200 dark:border-neutral-800 bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-300">
                Attempt {task.attempts.used} of {task.attempts.max}
              </span>
            </div>
          </div>

          {testContent ? (
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs text-neutral-500 font-mono">
                <span>Output Stream: stdout / stderr</span>
                <span className="text-emerald-600 dark:text-emerald-400 font-semibold">Verified Sandbox Run</span>
              </div>
              <pre className="p-4 rounded-xl border border-neutral-800 bg-neutral-950 font-mono text-xs text-neutral-200 whitespace-pre-wrap leading-relaxed max-h-96 overflow-y-auto">
                {testContent}
              </pre>
            </div>
          ) : (
            <div className="p-12 text-center text-xs text-neutral-500 space-y-2">
              <Terminal className="w-8 h-8 text-neutral-300 dark:text-neutral-700 mx-auto" />
              <p className="font-semibold text-neutral-700 dark:text-neutral-300">
                No sandbox test outputs recorded yet
              </p>
              <p className="max-w-md mx-auto">
                Test results and sandbox execution logs will populate as candidate patches are validated against the repository test suite.
              </p>
            </div>
          )}
        </div>
      )}

      {/* Tab 5: Pull Request */}
      {activeTab === "pr" && (
        <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-5 bg-white dark:bg-neutral-900 space-y-5">
          <div className="border-b border-neutral-100 dark:border-neutral-800 pb-3">
            <h3 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
              <GitPullRequest className="w-4 h-4 text-neutral-500" />
              <span>GitHub Pull Request</span>
            </h3>
            <p className="text-xs text-neutral-500 mt-0.5">
              Pull Request status on GitHub for the approved code fix
            </p>
          </div>

          {task.pr ? (
            <div className="border border-emerald-500/30 bg-emerald-500/5 rounded-xl p-6 space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <CheckCircle2 className="w-5 h-5 text-emerald-500 shrink-0" />
                    <span className="text-base font-bold text-neutral-900 dark:text-neutral-100">
                      Pull Request #{task.pr.number} Created
                    </span>
                  </div>
                  <p className="text-xs text-neutral-600 dark:text-neutral-400 font-mono">
                    Branch: {task.pr.branch}
                  </p>
                </div>

                <a
                  href={task.pr.url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-lg bg-neutral-900 hover:bg-neutral-800 dark:bg-neutral-100 dark:hover:bg-neutral-200 text-white dark:text-neutral-900 font-semibold text-xs transition-colors shrink-0"
                >
                  <span>Open on GitHub</span>
                  <ExternalLink className="w-3.5 h-3.5" />
                </a>
              </div>

              <div className="p-3 rounded-lg border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 text-xs text-neutral-600 dark:text-neutral-400 space-y-1">
                <p><strong>URL:</strong> <span className="font-mono">{task.pr.url}</span></p>
                <p>Human review reminder: GitResolve prepares pull requests with before/after test evidence. Merging requires explicit review and merge action on GitHub by repository maintainers.</p>
              </div>
            </div>
          ) : (
            <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-12 text-center space-y-2">
              <GitPullRequest className="w-8 h-8 text-neutral-300 dark:text-neutral-700 mx-auto" />
              <h4 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
                No Pull Request Created Yet
              </h4>
              <p className="text-xs text-neutral-500 max-w-md mx-auto">
                {task.status === "awaiting_approval"
                  ? "Review and approve the generated diff above to authorize creating the pull request on GitHub."
                  : task.status === "creating_pr"
                  ? "Opening the Pull Request on GitHub through the GitHub App gateway..."
                  : "A pull request will be opened after human approval of a verified fix."}
              </p>
            </div>
          )}
        </div>
      )}

      {/* Tab 6: Live Events Timeline */}
      {activeTab === "events" && (
        <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-5 bg-white dark:bg-neutral-900 space-y-4">
          <div className="flex items-center justify-between border-b border-neutral-100 dark:border-neutral-800 pb-3">
            <div>
              <h3 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
                <Terminal className="w-4 h-4 text-neutral-500" />
                <span>Sequential Agent Event Stream</span>
              </h3>
              <p className="text-xs text-neutral-500 mt-0.5">
                Real-time lifecycle events emitted by the agent worker and sandbox coordinator
              </p>
            </div>

            <span className="font-mono text-xs text-neutral-400">
              {events.length} event{events.length === 1 ? "" : "s"}
            </span>
          </div>

          <EventTimeline events={events} />
        </div>
      )}
      </section></div>
    </div>
  );
}

export default function TaskDetailsPage() {
  return (
    <ProtectedRoute>
      <Suspense fallback={<div className="py-24 text-center font-mono text-xs text-neutral-500">Loading workspace...</div>}>
        <TaskWorkspaceContent />
      </Suspense>
    </ProtectedRoute>
  );
}
