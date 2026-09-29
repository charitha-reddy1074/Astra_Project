"use client";

import { useEffect, useState, useCallback } from "react";
import { RefreshCw, CheckCircle2, AlertTriangle, XCircle, ArrowRight } from "lucide-react";
import { api } from "@/lib/api";

const RULE_LABEL = {
  duplicate_control: "Duplicate control code",
  missing_questions: "No questions",
  empty_statement: "Empty statement",
  orphan_control: "Not attached to a category",
  empty_category: "Empty sub-domain",
};

export default function ValidationPanel({ frameworkId, onJump }) {
  const [report, setReport]   = useState(null);
  const [loading, setLoading] = useState(true);

  const run = useCallback(() => {
    if (!frameworkId) return;
    setLoading(true);
    api.catalog.validate(frameworkId).then(setReport).catch(() => setReport(null)).finally(() => setLoading(false));
  }, [frameworkId]);

  useEffect(() => { run(); }, [run]);

  if (loading) return <div className="p-4 space-y-2">{[1,2,3].map(i => <div key={i} className="skeleton h-12 rounded-xl" />)}</div>;
  if (!report) return <p className="p-6 text-sm text-slate-400 text-center">Could not run validation.</p>;

  return (
    <div className="flex flex-col h-full">
      <div className="shrink-0 flex items-center gap-2 px-4 py-3 border-b border-slate-100">
        {report.ok
          ? <span className="badge badge-success"><CheckCircle2 size={12} /> No errors</span>
          : <span className="badge badge-critical"><XCircle size={12} /> {report.error_count} error{report.error_count !== 1 ? "s" : ""}</span>}
        {report.warning_count > 0 && (
          <span className="badge badge-medium"><AlertTriangle size={12} /> {report.warning_count} warning{report.warning_count !== 1 ? "s" : ""}</span>
        )}
        <button onClick={run} className="ml-auto p-1.5 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100" title="Re-run">
          <RefreshCw size={13} />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {report.issues.length === 0 ? (
          <div className="py-16 text-center">
            <CheckCircle2 size={28} className="text-emerald-300 mx-auto mb-3" />
            <p className="text-sm font-semibold text-slate-500">This framework is clean.</p>
          </div>
        ) : (
          report.issues.map((iss, i) => {
            const jumpable = iss.entity_type === "control" && iss.entity_id;
            return (
              <button
                key={i}
                onClick={() => jumpable && onJump?.(iss.entity_id)}
                disabled={!jumpable}
                className={`w-full text-left flex items-start gap-2.5 p-3 rounded-xl border transition-all ${
                  jumpable ? "hover:border-blue-300 hover:bg-blue-50/40 cursor-pointer" : "cursor-default"
                } ${iss.severity === "error" ? "border-rose-100 bg-rose-50/40" : "border-amber-100 bg-amber-50/30"}`}
              >
                {iss.severity === "error"
                  ? <XCircle size={14} className="text-rose-500 mt-0.5 shrink-0" />
                  : <AlertTriangle size={14} className="text-amber-500 mt-0.5 shrink-0" />}
                <div className="flex-1 min-w-0">
                  <p className="text-[11px] font-semibold text-slate-700">{RULE_LABEL[iss.rule] || iss.rule}</p>
                  <p className="text-xs text-slate-500 mt-0.5">{iss.message}</p>
                </div>
                {jumpable && <ArrowRight size={13} className="text-slate-300 mt-0.5 shrink-0" />}
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
