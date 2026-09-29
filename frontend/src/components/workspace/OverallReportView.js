"use client";

import { useEffect, useState, useMemo } from "react";
import { BarChart3, ClipboardCheck, X, Globe } from "lucide-react";
import { api } from "@/lib/api";
import { organizationKey } from "@/lib/users";

// ── Utilities ─────────────────────────────────────────────────────────────────
const fmt = (n) => Math.round(Number(n) || 0);
const barColor = (s) => s >= 80 ? "#16a34a" : s >= 50 ? "#3b82f6" : "#dc2626";

function SectionCard({ children, className = "" }) {
  return (
    <div className={`bg-white border border-slate-100 rounded-2xl ${className}`}
      style={{ boxShadow: "var(--shadow-card)" }}>
      {children}
    </div>
  );
}
function SectionLabel({ icon: Icon, children }) {
  return (
    <div className="flex items-center gap-2 mb-4">
      {Icon && <Icon size={14} className="text-slate-400" />}
      <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">{children}</p>
    </div>
  );
}

// Organization label for an assessment: prefer the explicit market label, fall
// back to the organization string, otherwise "—".
function organizationLabelOf(a) {
  return a.market_label || a.organization || "—";
}

// ── Main view ──────────────────────────────────────────────────────────────────
export default function OverallReportView() {
  const [selectedFwId,       setSelectedFwId]       = useState("all");
  const [assessments,        setAssessments]        = useState([]);
  const [frameworks,         setFrameworks]         = useState([]);
  const [loading,            setLoading]            = useState(true);

  useEffect(() => {
    Promise.all([api.assessments.list(), api.frameworks.list()])
      .then(([a, f]) => { setAssessments(a); setFrameworks(f); })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  // ── Assessments filtered by framework ────────────────────────────────────
  const scopedAssessments = useMemo(() => {
    return assessments.filter(a =>
      selectedFwId === "all" || (a.framework_ids || []).includes(selectedFwId)
    );
  }, [assessments, selectedFwId]);

  // ── Organization data map: every distinct organization label in scope ─────────────────
  const organizationData = useMemo(() => {
    const map = {};
    scopedAssessments.forEach(a => {
      const label = organizationLabelOf(a);
      const key = organizationKey(label);
      if (!map[key]) map[key] = { id: key, label, score: null, status: null, maturity_level: null, assessmentCount: 0 };
      const m = map[key];
      m.assessmentCount++;
      if (a.overall_score != null) {
        m.score = a.overall_score;
        m.status = a.status;
        m.maturity_level = a.maturity_level;
      }
    });
    return Object.values(map);
  }, [scopedAssessments]);

  // ranked organizations (highest score first, nulls last)
  const rankedOrganizations = useMemo(() =>
    [...organizationData].sort((a, b) => {
      if (a.score == null && b.score == null) return 0;
      if (a.score == null) return 1;
      if (b.score == null) return -1;
      return b.score - a.score;
    }), [organizationData]);

  // KPIs
  const kpis = useMemo(() => {
    const scored = organizationData.filter(m => m.score != null);
    const avg = scored.length ? Math.round(scored.reduce((s, m) => s + m.score, 0) / scored.length) : null;
    return {
      totalOrganizations:       organizationData.length,
      scoredOrganizations:      scored.length,
      avgScore:           avg,
      totalAssessments:   scopedAssessments.length,
      completedAssessments: scopedAssessments.filter(a => a.status === "completed").length,
    };
  }, [organizationData, scopedAssessments]);

  if (loading) return (
    <div className="p-6 space-y-4">
      <div className="skeleton h-8 w-48 rounded" />
      <div className="skeleton h-96 rounded-2xl" />
    </div>
  );

  return (
    <div className="min-h-full bg-slate-50 overflow-y-auto">
      <div className="p-6 max-w-5xl mx-auto space-y-5">

        {/* Title */}
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Globe size={14} className="text-slate-400" />
            <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Organization Analysis</span>
          </div>
          <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Organization Analysis</h2>
          <p className="text-sm text-slate-500 mt-0.5">
            {kpis.totalOrganizations} organization{kpis.totalOrganizations !== 1 ? "s" : ""} · {kpis.scoredOrganizations} scored · {kpis.totalAssessments} assessment{kpis.totalAssessments !== 1 ? "s" : ""} in scope
          </p>
        </div>

        {/* ── Filters row ──────────────────────────────────────────────────── */}
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <label className="text-[11px] font-bold text-slate-500 uppercase tracking-wider whitespace-nowrap">Framework</label>
            <select value={selectedFwId} onChange={e => setSelectedFwId(e.target.value)}
              className="text-sm border border-slate-200 rounded-xl px-3 py-2 bg-white text-slate-700 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-50">
              <option value="all">All frameworks</option>
              {frameworks.map(fw => (
                <option key={fw.id} value={fw.id}>{fw.name}</option>
              ))}
            </select>
          </div>

          {selectedFwId !== "all" && (
            <button onClick={() => setSelectedFwId("all")}
              className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-700 border border-slate-200 rounded-lg px-2.5 py-1.5 bg-white hover:bg-slate-50 transition-colors">
              <X size={11} /> Clear filters
            </button>
          )}
        </div>

        {/* KPI cards */}
        <div className="grid grid-cols-5 gap-3">
          {[
            { label: "Organizations", value: kpis.totalOrganizations, color: "text-slate-900" },
            { label: "Scored",  value: kpis.scoredOrganizations, color: "text-emerald-600" },
            {
              label: "Avg Score",
              value: kpis.avgScore != null ? `${kpis.avgScore}%` : "—",
              color: kpis.avgScore == null ? "text-slate-300"
                : kpis.avgScore >= 80 ? "text-emerald-600"
                : kpis.avgScore >= 50 ? "text-amber-600" : "text-red-600",
            },
            { label: "Assessments", value: kpis.totalAssessments, color: "text-slate-900" },
            { label: "Completed",   value: kpis.completedAssessments, color: "text-blue-600" },
          ].map(({ label, value, color }) => (
            <SectionCard key={label} className="p-4 text-center">
              <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">{label}</p>
              <p className={`text-2xl font-bold tabular-nums ${color}`}>{value}</p>
            </SectionCard>
          ))}
        </div>

        {/* Organization comparison */}
        {rankedOrganizations.length > 0 && (
          <SectionCard className="p-5">
            <SectionLabel icon={BarChart3}>Organization Scores</SectionLabel>
            <div className="space-y-2.5">
              {rankedOrganizations.map((m, i) => (
                <div key={m.id} className="flex items-center gap-3">
                  <span className={`text-[10px] font-bold w-5 text-center shrink-0 ${
                    i === 0 && m.score != null ? "text-emerald-600" :
                    i === rankedOrganizations.length - 1 && m.score != null ? "text-red-500" : "text-slate-400"
                  }`}>{m.score != null ? `#${i + 1}` : "—"}</span>
                  <span className="text-xs font-semibold text-slate-700 w-32 shrink-0 truncate">{m.label}</span>
                  <div className="flex-1 h-2.5 bg-slate-100 rounded-full overflow-hidden">
                    {m.score != null
                      ? <div className="h-full rounded-full" style={{ width: `${Math.min(fmt(m.score), 100)}%`, background: barColor(fmt(m.score)) }} />
                      : <div className="h-full bg-slate-200/50 rounded-full w-full" />}
                  </div>
                  {m.score != null
                    ? <span className="text-xs font-bold tabular-nums w-10 text-right" style={{ color: barColor(fmt(m.score)) }}>{fmt(m.score)}%</span>
                    : <span className="text-[10px] text-slate-300 w-10 text-right">N/A</span>}
                  <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full shrink-0 w-24 text-center ${
                    m.status === "completed"   ? "bg-emerald-50 text-emerald-700" :
                    m.status === "in_review"   ? "bg-blue-50 text-blue-700" :
                    m.status === "in_progress" ? "bg-amber-50 text-amber-700" :
                    "bg-slate-50 text-slate-400"
                  }`}>{m.status ? m.status.replace("_", " ") : "not started"}</span>
                </div>
              ))}
            </div>
          </SectionCard>
        )}

        {/* Assessments in scope table */}
        {scopedAssessments.length > 0 ? (
          <SectionCard className="overflow-hidden">
            <div className="p-5 pb-0">
              <SectionLabel icon={ClipboardCheck}>Assessments in Scope</SectionLabel>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-y border-slate-100 bg-slate-50/60">
                    {["Assessment", "Organization", "Status", "Score"].map(h => (
                      <th key={h} className="text-left px-5 py-2.5 text-[10px] font-bold text-slate-400 uppercase tracking-wider">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {scopedAssessments.map(a => (
                    <tr key={a.id} className="border-b border-slate-50 last:border-0 hover:bg-blue-50/30 transition-colors">
                      <td className="px-5 py-3 font-semibold text-slate-800">{a.name}</td>
                      <td className="px-5 py-3 text-slate-500">{organizationLabelOf(a)}</td>
                      <td className="px-5 py-3">
                        <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${
                          a.status === "completed"   ? "bg-emerald-50 text-emerald-700" :
                          a.status === "in_review"   ? "bg-blue-50 text-blue-700" :
                          a.status === "in_progress" ? "bg-amber-50 text-amber-700" :
                          "bg-slate-50 text-slate-500"
                        }`}>{(a.status || "").replace("_", " ")}</span>
                      </td>
                      <td className="px-5 py-3 font-bold tabular-nums"
                        style={a.overall_score != null ? { color: barColor(a.overall_score) } : {}}>
                        {a.overall_score != null ? `${a.overall_score}%` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </SectionCard>
        ) : (
          <SectionCard className="py-16 text-center">
            <ClipboardCheck size={28} className="text-slate-200 mx-auto mb-3" />
            <p className="text-sm font-medium text-slate-500">No assessments in scope.</p>
            <p className="text-xs text-slate-400 mt-1">
              {selectedFwId !== "all" ? "Try clearing the framework filter." : "Create assessments to see Organization comparison here."}
            </p>
          </SectionCard>
        )}

      </div>
    </div>
  );
}
