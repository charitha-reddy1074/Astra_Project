"use client";

import { useEffect, useState } from "react";
import { Building2, ChevronDown, ChevronUp, ClipboardCheck } from "lucide-react";
import { api } from "@/lib/api";

const STATUS_CONFIG = {
  draft:       { label: "Draft",       badge: "badge-neutral" },
  in_progress: { label: "In Progress", badge: "badge-medium"  },
  completed:   { label: "Completed",   badge: "badge-success" },
};

function OrgCard({ name, assessments }) {
  const [open, setOpen]   = useState(false);
  const completed         = assessments.filter((a) => a.status === "completed");
  const avgScore          = completed.length
    ? Math.round(completed.reduce((s, a) => s + (a.overall_score || 0), 0) / completed.length)
    : null;
  const pct = avgScore != null ? avgScore : null;
  const scoreColor = pct >= 60 ? "text-emerald-600" : pct >= 40 ? "text-amber-600" : "text-rose-600";

  return (
    <div className="bg-white border border-slate-100 rounded-2xl overflow-hidden transition-shadow hover:shadow-md" style={{ boxShadow: "var(--shadow-card)" }}>
      <button onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-4 px-5 py-4 hover:bg-slate-50/60 transition-colors text-left">
        <div className="w-10 h-10 bg-blue-50 rounded-xl flex items-center justify-center shrink-0">
          <Building2 size={16} className="text-blue-600" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-bold text-slate-900 truncate">{name}</p>
          <p className="text-xs text-slate-400 mt-0.5">
            {assessments.length} assessment{assessments.length !== 1 ? "s" : ""}
            {completed.length > 0 && <> · <span className="text-emerald-600">{completed.length} completed</span></>}
          </p>
        </div>
        {pct != null && (
          <div className="text-right shrink-0 mr-3">
            <p className={`text-xl font-bold tabular-nums ${scoreColor}`}>{pct}%</p>
            <p className="text-[10px] text-slate-400">avg score</p>
          </div>
        )}
        {open ? <ChevronUp size={14} className="text-slate-300 shrink-0" /> : <ChevronDown size={14} className="text-slate-300 shrink-0" />}
      </button>

      {open && (
        <div className="border-t border-slate-100 divide-y divide-slate-50">
          {assessments.map((a) => {
            const cfg = STATUS_CONFIG[a.status] || STATUS_CONFIG.draft;
            return (
              <div key={a.id} className="flex items-center gap-3 px-5 py-3 hover:bg-slate-50/60 transition-colors">
                <ClipboardCheck size={13} className="text-slate-300 shrink-0" />
                <span className="text-sm font-medium text-slate-700 flex-1 truncate">{a.name}</span>
                <span className={`badge ${cfg.badge} shrink-0`}>{cfg.label}</span>
                {(a.status === "in_review" || a.status === "completed") && a.overall_score != null && (
                  <span className="text-sm font-bold text-slate-700 tabular-nums shrink-0 w-12 text-right">{a.overall_score}%</span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function OrganizationsView() {
  const [assessments, setAssessments] = useState([]);
  const [loading,     setLoading]     = useState(true);
  const [error,       setError]       = useState(null);

  useEffect(() => {
    api.assessments.list().then(setAssessments).catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, []);

  const orgMap = {};
  for (const a of assessments) {
    const key = a.organization?.trim() || null;
    if (key) { if (!orgMap[key]) orgMap[key] = []; orgMap[key].push(a); }
  }
  const orgList  = Object.entries(orgMap).sort(([a], [b]) => a.localeCompare(b));
  const noOrg    = assessments.filter((a) => !a.organization?.trim());

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-5 fade-in-up">
      <div>
        <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Organizations</h2>
        <p className="text-sm text-slate-500 mt-1">
          {loading ? "Loading…" : `${orgList.length} organization${orgList.length !== 1 ? "s" : ""} · ${assessments.length} assessment${assessments.length !== 1 ? "s" : ""} total`}
        </p>
      </div>

      {error && (
        <div className="text-sm px-4 py-3 rounded-xl border bg-rose-50 border-rose-200 text-rose-700">{error}</div>
      )}

      {loading ? (
        <div className="space-y-3">{[1,2,3].map((i) => <div key={i} className="skeleton h-20 rounded-2xl" />)}</div>
      ) : orgList.length === 0 ? (
        <div className="bg-white border border-slate-100 rounded-2xl py-20 text-center" style={{ boxShadow: "var(--shadow-card)" }}>
          <Building2 size={28} className="text-slate-200 mx-auto mb-4" />
          <p className="text-sm font-semibold text-slate-500 mb-1">
            {noOrg.length > 0 ? "No named organizations" : "No assessments yet"}
          </p>
          <p className="text-xs text-slate-400 max-w-xs mx-auto">
            {noOrg.length > 0
              ? `${noOrg.length} assessment${noOrg.length !== 1 ? "s exist" : " exists"} without an organization. Set an organization when creating an assessment.`
              : "Organizations appear here once assessments are created with an organization name."}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {orgList.map(([name, asmts]) => <OrgCard key={name} name={name} assessments={asmts} />)}
          {noOrg.length > 0 && (
            <div className="flex items-center gap-3 bg-white border border-slate-100 rounded-xl px-5 py-3.5">
              <ClipboardCheck size={14} className="text-slate-300" />
              <p className="text-xs text-slate-400">
                {noOrg.length} assessment{noOrg.length !== 1 ? "s" : ""} without an organization assigned
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
