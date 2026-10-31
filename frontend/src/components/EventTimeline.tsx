import { EventView } from "@/types";
import { AlertCircle, ArrowRight, CheckCircle2, Cpu, GitPullRequest, Info, Terminal, TestTube2 } from "lucide-react";

interface EventTimelineProps {
  events: EventView[];
  isPolling?: boolean;
}

export function EventTimeline({ events, isPolling = false }: EventTimelineProps) {
  if (!events || events.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-8 text-slate-500 border border-slate-800 rounded-xl bg-slate-900/40">
        <Terminal className="w-8 h-8 mb-2 text-slate-600 animate-pulse" />
        <p className="text-sm">Awaiting first execution event from background worker...</p>
      </div>
    );
  }

  const getEventIcon = (type: string, level: string) => {
    if (level === "error") return <AlertCircle className="w-4 h-4 text-rose-400" />;
    if (type === "stage_change") return <ArrowRight className="w-4 h-4 text-sky-400" />;
    if (type === "llm_call") return <Cpu className="w-4 h-4 text-purple-400" />;
    if (type === "test_run") return <TestTube2 className="w-4 h-4 text-amber-400" />;
    if (type === "pr_created") return <GitPullRequest className="w-4 h-4 text-emerald-400" />;
    if (type === "status_change") return <CheckCircle2 className="w-4 h-4 text-indigo-400" />;
    return <Info className="w-4 h-4 text-slate-400" />;
  };

  return (
    <div className="border border-slate-800 rounded-xl overflow-hidden bg-slate-950 flex flex-col h-full shadow-inner">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-slate-900/90 border-b border-slate-800 text-xs">
        <div className="flex items-center gap-2">
          <Terminal className="w-4 h-4 text-sky-400" />
          <span className="font-semibold text-slate-200">Execution Events Stream</span>
          <span className="px-1.5 py-0.5 rounded-full bg-slate-800 text-slate-400 font-mono text-[10px]">
            {events.length} events
          </span>
        </div>
        {isPolling && (
          <div className="flex items-center gap-1.5 text-emerald-400 font-mono text-[11px]">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping" />
            <span>LIVE</span>
          </div>
        )}
      </div>

      {/* Events log list */}
      <div className="overflow-y-auto p-3 space-y-2 max-h-[460px] font-mono text-xs">
        {events.map((ev) => {
          const time = new Date(ev.ts).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          });

          return (
            <div
              key={ev.seq}
              className={`flex items-start gap-3 p-2.5 rounded-lg border transition-all ${
                ev.level === "error"
                  ? "bg-rose-950/20 border-rose-900/50 text-rose-200"
                  : ev.level === "warn"
                  ? "bg-amber-950/20 border-amber-900/50 text-amber-200"
                  : "bg-slate-900/50 border-slate-850 text-slate-300"
              }`}
            >
              <div className="mt-0.5 shrink-0">{getEventIcon(ev.type, ev.level)}</div>

              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2 mb-1">
                  <div className="flex items-center gap-2">
                    <span className="px-1.5 py-0.2 rounded bg-slate-800 text-slate-400 text-[10px] uppercase font-semibold">
                      {ev.stage}
                    </span>
                    <span className="text-[11px] text-slate-500">{ev.type}</span>
                  </div>
                  <span className="text-[11px] text-slate-500 shrink-0">{time}</span>
                </div>

                <p className="text-slate-200 break-words leading-relaxed">{ev.message}</p>

                {ev.data && Object.keys(ev.data).length > 0 && (
                  <pre className="mt-1.5 p-2 rounded bg-slate-950/90 text-slate-400 text-[11px] overflow-x-auto border border-slate-800">
                    {JSON.stringify(ev.data, null, 2)}
                  </pre>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
