import { FileCode, FileDiff } from "lucide-react";

interface DiffViewerProps {
  diffText: string;
  filename?: string;
}

export function DiffViewer({ diffText, filename }: DiffViewerProps) {
  if (!diffText || diffText.trim().length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-8 text-slate-500 border border-slate-800 rounded-xl bg-slate-900/40">
        <FileCode className="w-8 h-8 mb-2 text-slate-600" />
        <p className="text-sm">No code changes recorded for this task.</p>
      </div>
    );
  }

  const lines = diffText.split("\n");

  let additions = 0;
  let deletions = 0;
  lines.forEach((l) => {
    if (l.startsWith("+") && !l.startsWith("+++")) additions++;
    if (l.startsWith("-") && !l.startsWith("---")) deletions++;
  });

  return (
    <div className="border border-slate-800 rounded-xl overflow-hidden bg-slate-950 font-mono text-xs shadow-inner">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-slate-900/90 border-b border-slate-800 text-slate-300">
        <div className="flex items-center gap-2">
          <FileDiff className="w-4 h-4 text-sky-400" />
          <span className="font-semibold text-slate-200">{filename || "Unified Proposed Patch"}</span>
        </div>
        <div className="flex items-center gap-2.5 text-xs">
          <span className="text-emerald-400 font-medium">+{additions}</span>
          <span className="text-rose-400 font-medium">-{deletions}</span>
        </div>
      </div>

      {/* Code viewport */}
      <div className="overflow-x-auto p-2 max-h-[500px]">
        <table className="w-full border-collapse">
          <tbody>
            {lines.map((line, idx) => {
              const isAdd = line.startsWith("+") && !line.startsWith("+++");
              const isDel = line.startsWith("-") && !line.startsWith("---");
              const isHdr = line.startsWith("@@") || line.startsWith("---") || line.startsWith("+++");

              let rowClass = "text-slate-300 hover:bg-slate-900/50";
              let linePrefix = " ";

              if (isAdd) {
                rowClass = "bg-emerald-950/30 text-emerald-300 border-l-2 border-emerald-500";
                linePrefix = "+";
              } else if (isDel) {
                rowClass = "bg-rose-950/30 text-rose-300 border-l-2 border-rose-500";
                linePrefix = "-";
              } else if (isHdr) {
                rowClass = "text-sky-400/90 bg-sky-950/20 font-semibold";
              }

              return (
                <tr key={idx} className={`${rowClass} transition-colors leading-relaxed`}>
                  <td className="w-12 select-none text-right pr-3 text-slate-600 font-mono text-[11px] py-0.5">
                    {idx + 1}
                  </td>
                  <td className="whitespace-pre py-0.5 pl-2 font-mono break-all">
                    {line}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
