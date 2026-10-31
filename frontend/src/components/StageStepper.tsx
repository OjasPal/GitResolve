import { TaskStage, TaskStatus } from "@/types";
import { Check, Circle } from "lucide-react";

interface StageStepperProps {
  currentStage: TaskStage;
  status: TaskStatus;
}

const STAGES: { id: TaskStage; label: string }[] = [
  { id: "intake", label: "Intake" },
  { id: "plan", label: "Plan" },
  { id: "setup", label: "Setup" },
  { id: "baseline", label: "Baseline" },
  { id: "locate", label: "Locate" },
  { id: "reproduce", label: "Reproduce" },
  { id: "patch", label: "Patch" },
  { id: "test", label: "Test" },
  { id: "evaluate", label: "Evaluate" },
  { id: "policy_gate", label: "Policy Gate" },
  { id: "awaiting_approval", label: "Review Diff" },
  { id: "pr", label: "Open PR" },
  { id: "completed", label: "Done" },
];

export function StageStepper({ currentStage, status }: StageStepperProps) {
  const currentIndex = STAGES.findIndex((s) => s.id === currentStage);
  const isFailed = status.startsWith("failed_") || status === "timed_out" || status === "worker_lost";

  return (
    <div className="w-full overflow-x-auto py-2">
      <div className="flex items-center min-w-[700px] justify-between">
        {STAGES.map((stage, idx) => {
          const isPassed = idx < currentIndex || (status === "completed" && idx === currentIndex);
          const isCurrent = idx === currentIndex && status !== "completed";

          return (
            <div key={stage.id} className="flex flex-col items-center relative flex-1">
              {/* Connector line */}
              {idx > 0 && (
                <div
                  className={`absolute top-3.5 -left-1/2 w-full h-[2px] -z-0 transition-colors ${
                    idx <= currentIndex
                      ? isFailed && idx === currentIndex
                        ? "bg-rose-500/50"
                        : "bg-sky-500/80"
                      : "bg-slate-800"
                  }`}
                />
              )}

              {/* Node indicator */}
              <div
                className={`relative z-10 flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold transition-all ${
                  isPassed
                    ? "bg-sky-500 text-slate-950 shadow-sm shadow-sky-500/50"
                    : isCurrent
                    ? isFailed
                      ? "bg-rose-500 text-white ring-4 ring-rose-500/20"
                      : "bg-indigo-600 text-white ring-4 ring-indigo-500/30 animate-pulse"
                    : "bg-slate-900 border border-slate-700 text-slate-500"
                }`}
              >
                {isPassed ? (
                  <Check className="w-3.5 h-3.5 stroke-[3]" />
                ) : isCurrent ? (
                  <span className="w-2 h-2 rounded-full bg-white" />
                ) : (
                  <Circle className="w-2 h-2 text-slate-600" />
                )}
              </div>

              {/* Stage label */}
              <span
                className={`mt-2 text-[11px] tracking-tight font-medium text-center whitespace-nowrap ${
                  isCurrent
                    ? isFailed
                      ? "text-rose-400 font-semibold"
                      : "text-sky-300 font-semibold"
                    : isPassed
                    ? "text-slate-300"
                    : "text-slate-600"
                }`}
              >
                {stage.label}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
