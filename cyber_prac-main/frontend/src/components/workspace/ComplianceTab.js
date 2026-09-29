"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle, Archive, BookOpen, ChevronDown, ChevronRight,
  FileSearch, FlaskConical, Gauge, Layers, RefreshCw, ShieldCheck, Wallet,
} from "lucide-react";
import { api } from "@/lib/api";
import { canDo } from "@/lib/auth";

const STATUS_BADGE = {
  PASS:                    "badge-success",
  PARTIAL:                 "badge-medium",
  FAIL:                    "badge-critical",
  INSUFFICIENT_EVIDENCE:   "badge-high",
  NOT_APPLICABLE:          "badge-neutral",
};
const STATUS_LABEL = {
  PASS: "Pass", PARTIAL: "Partial", FAIL: "Fail",
  INSUFFICIENT_EVIDENCE: "Insufficient evidence", NOT_APPLICABLE: "N/A",
};
const BAND_BADGE = {
  STRONG: "badge-success", MEDIUM: "badge-medium",
  WEAK: "badge-high", MISSING: "badge-neutral",
};
const CLASS_BADGE = {
  ESCALATION_REQUIRED:   "badge-critical",
  RECURRING_FINDING:     "badge-critical",
  PATTERN_DETECTED:      "badge-medium",
  KNOWN_EXCEPTION:       "badge-neutral",
  RESOLVED_RECURRING_FINDING: "badge-medium",
};
const SEVERITY_BADGE = {
  critical: "badge-critical", high: "badge-high",
  medium: "badge-medium", low: "badge-neutral",
};

const fmt = (n) => {
  if (n == null || Number.isNaN(n)) return "—";
  return Math.round(n * 10) / 10;
};

function scoreBar(score) {
  const s = Math.max(0, Math.min(100, Math.round((score || 0) * 100)));
  const color = s >= 75 ? "bg-emerald-500" : s >= 50 ? "bg-amber-400" : "bg-rose-500";
  return { s, color };
}

function StatTile({ label, value, sub, icon: Icon, accent }) {
  return (
    <div className="bg-white border border-slate-100 rounded-xl py-3 px-4" style={{ boxShadow: "var(--shadow-card)" }}>
      <div className="flex items-center gap-2">
        <span className={`flex h-7 w-7 items-center justify-center rounded-lg ${accent}`}>
          <Icon size={13} />
        </span>
        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">{label}</p>
      </div>
      <p className="mt-2 font-display text-2xl font-extrabold leading-none tracking-tight text-slate-900 tabular-nums">
        {value}
      </p>
      {sub && <p className="mt-1.5 text-[11px] font-medium text-slate-400">{sub}</p>}
    </div>
  );
}

function SectionCard({ children, className = "" }) {
  return (
    <div className={`bg-white border border-slate-100 rounded-2xl ${className}`} style={{ boxShadow: "var(--shadow-card)" }}>
      {children}
    </div>
  );
}

function SectionLabel({ icon: Icon, children, right }) {
  return (
    <div className="flex items-center gap-2 px-5 pt-5 pb-3">
      <Icon size={15} className="text-blue-600 shrink-0" />
      <p className="text-[10px] font-bold text-slate-400 tracking-widest uppercase">{children}</p>
      {right && <span className="ml-auto">{right}</span>}
    </div>
  );
}

export default function ComplianceTab({ assessmentId, role }) {
  const canRun = canDo(role, "runCompliancePipeline");
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [running, setRunning] = useState(false);
  const [runMsg, setRunMsg] = useState(null);
  const [expanded, setExpanded] = useState(() => new Set());

const load = useCallback(async () => {
    try {
      const res = await api.assessments.complianceReport(assessmentId);
      if (res && res.ok === false) {
        setError(res.error || "The compliance report is unavailable.");
        setReport(null);
      } else {
        setReport(res);
        setError(null);
      }
    } catch (e) {
      setError(e.message || "Could not load the compliance report.");
      setReport(null);
    } finally {
      setLoading(false);
    }
  }, [assessmentId]);

  useEffect(() => { load(); }, [load]);

const toggleRow = (key) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  const toggleAll = (fw) => {
    setExpanded((prev) => {
      const prefix = `${fw.framework}\u0000`;
      const keys = (fw.controls || []).map((c) => prefix + ((c.control || {}).control_id || ""));
      const allOpen = keys.every((k) => prev.has(k));
      const next = new Set([...prev].filter((k) => !k.startsWith(prefix)));
      if (!allOpen) keys.forEach((k) => next.add(k));
      return next;
    });
  };

  const handleRun = async () => {
    setRunning(true);
    setRunMsg(null);
    try {
      const res = await api.assessments.runCompliancePipeline(assessmentId, {});
      if (res && res.ok === false) {
        setRunMsg(res.error || "The pipeline could not run right now.");
      } else {
        setReport(res);
        setExpanded(new Set());
        setRunMsg("Pipeline run complete — readings recorded and rendered below.");
      }
    } catch (e) {
      setRunMsg(e.message || "The pipeline could not run right now.");
    } finally {
      setRunning(false);
    }
  };

  const summary = report?.summary || {};
  const controls = (report?.frameworks || []).flatMap((f) =>
    (f.controls || []).map((c) => ({ ...c, _framework: f }))
  );
  const submitted = (report?.metrics || {}).semantic_evaluations;
  const cached = (report?.metrics || {}).semantic_cache_hits;

  return (
    <div className="mx-auto max-w-6xl p-6 space-y-6">
      <div className="bg-white border border-slate-100 rounded-2xl p-5 flex items-center gap-3" style={{ boxShadow: "var(--shadow-card)" }}>
        <span className="icon-tile flex h-10 w-10 items-center justify-center rounded-xl">
          <ShieldCheck size={20} />
        </span>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-display text-lg font-extrabold text-slate-900 tracking-tight">Compliance Report</h3>
            {report && (
              <span className={`badge ${submitted > 0 ? "badge-success" : "badge-neutral"}`}>
                {submitted > 0 ? "Pipeline run recorded" : "Deterministic only"}
              </span>
            )}
          </div>
          <p className="text-xs text-slate-400 mt-0.5">
            {report
              ? `Recorded readings under ${(report.assurance_level || "POINT_IN_TIME").replace("_", " ").toLowerCase()} assurance · model calls ${submitted || 0}, cache hits ${cached || 0}`
              : "Run the assessment pipeline to record deterministic + semantic + memory readings."}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {canRun && (
            <button onClick={handleRun} disabled={running || loading}
              className="btn-primary text-xs px-3 py-1.5 shrink-0">
              <FlaskConical size={12} /> {running ? "Running…" : "Run Pipeline"}
            </button>
          )}
          <button onClick={load} disabled={loading}
            className="btn-secondary text-xs px-3 py-1.5 shrink-0">
            <RefreshCw size={12} /> Refresh
          </button>
        </div>
      </div>

      {runMsg && <div className="alert text-sm">{runMsg}</div>}
      {error && !report && (
        <div className="bg-white border border-slate-100 rounded-2xl py-16 text-center" style={{ boxShadow: "var(--shadow-card)" }}>
          <AlertTriangle size={22} className="text-amber-400 mx-auto mb-2" />
          <p className="text-sm text-slate-500">Could not load the compliance report.</p>
          <p className="text-xs text-slate-400 mt-1">{error}</p>
        </div>
      )}

      {loading && <div className="skeleton h-96 rounded-2xl" />}

      {!loading && report && (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <StatTile label="Controls reviewed" value={summary.total ?? 0}
              sub={`${(report.frameworks || []).length} framework(s)`} icon={Layers} accent="brand-tile" />
            <StatTile label="Conclusive" value={`${fmt(summary.conclusive_pct)}%`}
              sub={`${summary.conclusive ?? 0} conclusive verdicts`} icon={ShieldCheck} accent="brand-tile" />
            <StatTile label="Mean confidence" value={fmt(summary.mean_confidence)}
              sub="across evaluated controls" icon={Gauge} accent="brand-tile" />
            <StatTile label="Open findings" value={summary.open_findings ?? 0}
              sub={`${summary.high_risk_findings ?? 0} high-risk · ${summary.recurring ?? 0} recurring`} icon={AlertTriangle} accent="bg-rose-600/10" />
          </div>

          <div className="grid gap-6 lg:grid-cols-3">
            <div className="lg:col-span-2 space-y-6">
              {(report.frameworks || []).length === 0 ? (
                <SectionCard className="py-16 text-center">
                  <div className="empty-state">
                    <FileSearch size={22} className="mx-auto mb-2 text-slate-300" />
                    <p className="text-sm text-slate-500">No evaluations recorded yet.</p>
                    <p className="text-xs text-slate-400 mt-1">
                      Run the assessment pipeline to evaluate, normalise and review every selected framework.
                    </p>
                  </div>
                </SectionCard>
              ) : (
(report.frameworks || []).map((fw) => (
                  <FrameworkCard key={fw.framework} fw={fw} expanded={expanded}
                    onToggle={toggleRow} onToggleAll={toggleAll} />
                ))
              )}
            </div>

            <CostLedger metrics={report.metrics || {}} />
          </div>
        </>
      )}
    </div>
  );
}

function FrameworkCard({ fw, expanded, onToggle, onToggleAll }) {
  const s = fw.summary || {};
  const prefix = `${fw.framework}\u0000`;
  const rowKeys = (fw.controls || []).map((c) => prefix + ((c.control || {}).control_id || ""));
  const allExpanded = rowKeys.length > 0 && rowKeys.every((k) => expanded.has(k));
  return (
    <SectionCard>
      <div className="flex items-center gap-3 px-5 pt-5 pb-3 border-b border-slate-100">
        <span className="icon-tile flex h-9 w-9 items-center justify-center rounded-xl">
          <BookOpen size={16} />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-bold text-slate-800">{fw.framework_name || fw.framework}</p>
          <p className="text-[11px] text-slate-400 font-medium">
            {fw.framework_code} · assurance {fw.assurance_level || "POINT_IN_TIME"}
          </p>
        </div>
        <span className="ml-auto badge badge-neutral">{s.total ?? 0} controls</span>
      </div>

      <div className="grid grid-cols-2 gap-3 px-5 py-3 border-b border-slate-100 sm:grid-cols-4">
        <MiniStat label="Conclusive" value={`${fmt(s.conclusive_pct)}%`} />
        <MiniStat label="Mean confidence" value={fmt(s.mean_confidence)} />
        <MiniStat label="Open findings" value={s.open_findings ?? 0} />
        <MiniStat label="Recurring" value={s.recurring ?? 0} />
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm report-table">
          <thead>
            <tr className="border-b border-slate-100">
              <th className="w-8" />
              <th className="text-left px-3 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-wider">Control</th>
              <th className="text-left px-3 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-wider">Status</th>
              <th className="text-left px-3 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-wider">Evidence</th>
              <th className="text-left px-3 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-wider">Confidence</th>
              <th className="text-left px-3 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-wider">Semantic</th>
              <th className="text-left px-3 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-wider">History</th>
              <th className="text-left px-3 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-wider">Finding</th>
            </tr>
          </thead>
          <tbody>
            {(fw.controls || []).map((c) => <ControlRow key={c.control?.control_id} fw={fw} c={c} expanded={expanded} onToggle={onToggle} />)}
          </tbody>
        </table>
      </div>

<div className="flex justify-end px-5 py-2 border-t border-slate-100">
        <button onClick={() => onToggleAll(fw)}
          className="text-[11px] font-semibold text-blue-600 hover:underline flex items-center gap-1">
          {allExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          {allExpanded ? "Collapse all" : "Expand all details"}
        </button>
      </div>
    </SectionCard>
  );
}

function MiniStat({ label, value }) {
  return (
    <div className="bg-slate-50 border border-slate-100 rounded-lg px-3 py-2">
      <p className="text-[9px] font-bold text-slate-400 uppercase tracking-wider">{label}</p>
      <p className="text-sm font-extrabold text-slate-800 tabular-nums">{value}</p>
    </div>
  );
}

function ControlRow({ fw, c, expanded, onToggle }) {
  const ctrl = c.control || {};
  const eq = c.evidence_quality || {};
  const ctx = c.historical_context || {};
  const sem = c.semantic_advisory || null;
  const finding = c.finding || null;
  const bar = scoreBar(c.evidence_score);

  const rowKey = `${fw.framework}\u0000${ctrl.control_id}`;
  const isOpen = expanded.has(rowKey);

  return (
    <>
      <tr className="border-b border-slate-50 last:border-0 hover:bg-slate-50/50 cursor-pointer" onClick={() => onToggle(rowKey)}>
        <td className="px-2 py-3 text-slate-400">
          {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </td>
        <td className="px-3 py-3 align-top">
          <p className="text-xs font-bold text-slate-800 tabular-nums whitespace-nowrap">{ctrl.control_id}</p>
          <p className="text-[11px] text-slate-400 mt-0.5 max-w-[220px] truncate">{ctrl.statement}</p>
        </td>
        <td className="px-3 py-3 align-top">
          <span className={`badge ${STATUS_BADGE[c.status] || "badge-neutral"} whitespace-nowrap`}>
            {STATUS_LABEL[c.status] || c.status}
          </span>
          {c.is_conclusive && <p className="text-[9px] text-emerald-600 font-bold mt-1 uppercase tracking-wide">Conclusive</p>}
        </td>
        <td className="px-3 py-3 align-top">
          <div className="flex items-center gap-2 min-w-[110px]">
            <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
              <div className={`h-full rounded-full ${bar.color}`} style={{ width: `${bar.s}%` }} />
            </div>
            <span className="text-[11px] font-bold text-slate-700 tabular-nums w-9 text-right">{bar.s}%</span>
          </div>
          <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
            <span className={`badge ${BAND_BADGE[eq.band] || "badge-neutral"} text-[9px]`}>{eq.band || "MISSING"}</span>
            {eq.is_implementation_grade && (
              <span className="badge badge-success text-[9px]">implementation grade</span>
            )}
          </div>
        </td>
        <td className="px-3 py-3 align-top">
          <span className="badge badge-neutral whitespace-nowrap text-[10px]">
            {c.confidence_band || "—"} · {fmt(c.confidence)}
          </span>
        </td>
        <td className="px-3 py-3 align-top">
          {sem ? (
            <>
              <div className="flex items-center gap-1.5">
                <span className={`badge ${STATUS_BADGE[sem.decision] || "badge-neutral"} text-[9px]`}>
                  {sem.model_invoked ? (sem.decision || "fallback") : "not run"}
                </span>
                {sem.model_invoked && !sem.cache_hit && <span className="text-[9px] text-slate-400 font-bold uppercase">called</span>}
                {sem.cache_hit && <span className="text-[9px] text-emerald-600 font-bold uppercase">cached</span>}
              </div>
              {sem.more_cautious_than_deterministic && (
                <span className="badge badge-medium text-[9px] mt-1">more cautious</span>
              )}
            </>
          ) : <span className="text-[10px] text-slate-400">—</span>}
        </td>
        <td className="px-3 py-3 align-top">
          {ctx.classification ? (
            <>
              <span className={`badge ${CLASS_BADGE[ctx.classification] || "badge-neutral"} text-[9px]`}>{ctx.classification}</span>
              {ctx.occurrence > 0 && <p className="text-[9px] text-slate-400 mt-1 tabular-nums">×{ctx.occurrence}</p>}
            </>
          ) : <span className="text-[10px] text-slate-400">—</span>}
        </td>
        <td className="px-3 py-3 align-top">
          {finding ? (
            <>
              <span className={`badge ${SEVERITY_BADGE[finding.severity] || "badge-neutral"} text-[9px]`}>{finding.severity}</span>
              <p className="text-[10px] text-slate-500 mt-1 max-w-[160px] truncate">{finding.title}</p>
            </>
          ) : <span className="text-[10px] text-slate-400">—</span>}
        </td>
      </tr>
      {isOpen && (
        <tr className="border-b border-slate-50 bg-slate-50/40 last:border-0">
          <td />
          <td colSpan={7} className="px-3 py-4">
            <div className="grid gap-4 md:grid-cols-2">
              <DetailBlock title="Requirement">
                <p className="text-xs text-slate-600 leading-relaxed">{c.requirement?.text || ctrl.requirement?.text}</p>
              </DetailBlock>
              {c.requirement?.clauses?.length > 0 && (
                <DetailBlock title="Clauses">
                  <ul className="list-disc list-inside space-y-0.5">
                    {(c.requirement.clauses || []).map((cl, i) => (
                      <li key={i} className="text-xs text-slate-600">{cl}</li>
                    ))}
                  </ul>
                </DetailBlock>
              )}
              <DetailBlock title="Reasoning">
                <p className="text-xs text-slate-600 leading-relaxed">{c.reasoning || "—"}</p>
              </DetailBlock>
              <DetailBlock title="Gaps">
                {(c.gaps || []).length === 0 ? <p className="text-xs text-slate-400">None recorded.</p> : (
                  <ul className="list-disc list-inside space-y-0.5">
                    {(c.gaps || []).map((g, i) => <li key={i} className="text-xs text-slate-600">{g}</li>)}
                  </ul>
                )}
              </DetailBlock>
              {(c.evidence?.items?.length > 0) && (
                <DetailBlock title="Evidence items">
                  <ul className="space-y-1">
                    {(c.evidence.items || []).map((it, i) => (
                      <li key={i} className="text-xs text-slate-600 flex items-center gap-2">
                        <span className="badge badge-neutral text-[9px]">{it.format || "unknown"}</span>
                        <span className="truncate">{it.source_name || it.summary || "attached"}</span>
                        <span className="ml-auto text-[9px] text-slate-400 tabular-nums">{it.chars} chars</span>
                      </li>
                    ))}
                  </ul>
                </DetailBlock>
              )}
              {sem && sem.model_invoked && (
                <DetailBlock title="Semantic advisory (Groq)">
                  <p className="text-xs text-slate-600 leading-relaxed">{sem.reasoning || "No reading recorded."}</p>
                  {(sem.identified_gaps || []).length > 0 && (
                    <ul className="list-disc list-inside space-y-0.5 mt-1.5">
                      {(sem.identified_gaps || []).map((g, i) => <li key={i} className="text-[11px] text-slate-500">{g}</li>)}
                    </ul>
                  )}
                  {sem.fallback_reason && <p className="text-[11px] text-amber-600 mt-1.5">{sem.fallback_reason}</p>}
                </DetailBlock>
              )}
              {finding && (
                <DetailBlock title="Finding">
                  <div className="flex items-center gap-2">
                    <span className={`badge ${SEVERITY_BADGE[finding.severity] || "badge-neutral"}`}>{finding.severity}</span>
                    <span className={`badge badge-neutral`}>{finding.status}</span>
                  </div>
                  <p className="text-xs text-slate-600 mt-1.5 leading-relaxed">{finding.gap_description}</p>
                </DetailBlock>
              )}
              <DetailBlock title="Recommendation">
                <p className="text-xs text-slate-600 leading-relaxed">{c.recommendation?.text || "—"}</p>
                {(c.recommendation?.remediation_actions || []).length > 0 && (
                  <ul className="list-disc list-inside space-y-0.5 mt-1.5">
                    {(c.recommendation.remediation_actions || []).map((a, i) => <li key={i} className="text-[11px] text-slate-500">{a}</li>)}
                  </ul>
                )}
                {c.recommendation?.remediation_status && (
                  <p className="text-[11px] text-slate-400 mt-1.5 font-medium">{c.recommendation.remediation_status}</p>
                )}
              </DetailBlock>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function DetailBlock({ title, children }) {
  return (
    <div>
      <p className="text-[9px] font-bold text-slate-400 tracking-widest uppercase mb-1">{title}</p>
      {children}
    </div>
  );
}

function CostLedger({ metrics }) {
  const rows = [
    { label: "Frameworks", value: metrics.frameworks },
    { label: "Controls reviewed", value: metrics.controls_reviewed },
    { label: "Evidence files seen", value: metrics.evidence_files_seen },
    { label: "Deduplicated by hash", value: metrics.deduplicated },
    { label: "Dropped over budget", value: metrics.dropped_over_budget },
    { label: "Truncated", value: metrics.truncated },
    { label: "Chunks built", value: metrics.chunks_built },
    { label: "Semantic evaluations", value: metrics.semantic_evaluations },
    { label: "Cache hits", value: metrics.semantic_cache_hits },
    { label: "Controls skipped", value: metrics.controls_skipped_semantic },
    { label: "Memory records written", value: metrics.memory_records_written },
  ];
  const tiers = Object.entries(metrics.llm_calls_by_tier || {});
  return (
    <SectionCard className="h-fit">
      <SectionLabel icon={Wallet}>Cost ledger</SectionLabel>
      <div className="px-5 pb-4">
        <div className="flex items-center justify-between rounded-xl bg-emerald-50 border border-emerald-100 px-4 py-3 mb-3">
          <span className="text-xs font-bold text-emerald-800">LLM calls avoided</span>
          <span className="text-sm font-extrabold text-emerald-700 tabular-nums">{metrics.avoided_llm_calls ?? 0}</span>
        </div>
        <dl className="space-y-1.5">
          {rows.map((r) => (
            <div key={r.label} className="flex items-center justify-between gap-4">
              <dt className="text-[11px] font-medium text-slate-500">{r.label}</dt>
              <dd className="text-[11px] font-bold text-slate-800 tabular-nums">{r.value ?? 0}</dd>
            </div>
          ))}
        </dl>
        {tiers.length > 0 && (
          <div className="mt-3 pt-3 border-t border-slate-100">
            <p className="text-[9px] font-bold text-slate-400 tracking-widest uppercase mb-1.5">Model calls by tier</p>
            {tiers.map(([tier, count]) => (
              <div key={tier} className="flex items-center justify-between gap-4 py-0.5">
                <dt className="text-[11px] font-medium text-slate-500">{tier}</dt>
                <dd className="text-[11px] font-bold text-slate-800 tabular-nums">{count}</dd>
              </div>
            ))}
          </div>
        )}
        <div className="mt-3 flex items-start gap-1.5 text-[10px] text-slate-400">
          <Archive size={11} className="shrink-0 mt-0.5" />
          <p>Read-only refresh renders recorded readings and never calls a model.</p>
        </div>
      </div>
    </SectionCard>
  );
}
