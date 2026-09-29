"use client";

import { useEffect, useState, useMemo } from "react";
import { ClipboardCheck, BookOpen, AlertTriangle, BarChart3, Plus, ArrowRight, Inbox, Building2, Shield, TrendingUp, CheckCircle2, Globe, FileText } from "lucide-react";
import { api } from "@/lib/api";
import { canCreate, canView } from "@/lib/auth";
import { DonutChart, BarChart, CountUp } from "@/components/ui/Charts";

const REVIEWER_ACTIONS = [
  { label: "New Assessment", icon: Plus, view: "assessments" },
  { label: "Inbox", icon: Inbox, view: "inbox" },
  { label: "Reports", icon: BarChart3, view: "reports" },
  { label: "Org Analysis", icon: Globe, view: "overall_report" },
];
const CONTRIBUTOR_ACTIONS = [
  { label: "My Documents", icon: Inbox, view: "my_work" },
  { label: "Upload Evidence", icon: FileText, view: "my_work" },
];

const ROLE_ACTIONS = {
  org_owner:            [...REVIEWER_ACTIONS, { label: "Users", icon: Building2, view: "users" }],
  compliance_manager:   REVIEWER_ACTIONS,
  security_manager:     REVIEWER_ACTIONS,
  auditor:              REVIEWER_ACTIONS,
  team_member:          CONTRIBUTOR_ACTIONS,
  evidence_contributor: CONTRIBUTOR_ACTIONS,
};

function KpiCard({ label, value, sub, icon: Icon, accent, loading, onClick }) {
  return (
    <div onClick={onClick}
      className={`group card relative overflow-hidden p-4 ${onClick ? "cursor-pointer card-hover" : ""}`}>
      {/* gold sweep on hover */}
      <span
        className="pointer-events-none absolute inset-x-0 top-0 h-[3px] origin-left scale-x-0 group-hover:scale-x-100 transition-transform duration-300 ease-out"
        style={{ background: "var(--grad-gold)" }}
      />
      <div className="flex items-start justify-between mb-3">
        <p className="eyebrow leading-tight pr-2">{label}</p>
        <div className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 ${accent} transition-transform duration-300 group-hover:scale-110 group-hover:-rotate-3`}
          style={{ boxShadow: "var(--shadow-xs)" }}>
          <Icon size={13} className="text-white" />
        </div>
      </div>
      <p className="font-display text-[26px] font-extrabold text-slate-900 tabular-nums leading-none" style={{ letterSpacing: "-0.035em" }}>
        {loading || value == null ? <span className="skeleton inline-block w-12 h-7" />
          : typeof value === "number" ? <CountUp to={value} /> : value}
      </p>
      {sub && <p className="text-[10.5px] text-slate-400 mt-2 font-semibold">{sub}</p>}
    </div>
  );
}

export default function DashboardView({ setActiveView, role }) {
  const [frameworks,      setFrameworks]      = useState([]);
  const [allAssessments,  setAllAssessments]  = useState([]);
  const [findingsMap,     setFindingsMap]     = useState({});
  const [loading,         setLoading]         = useState(true);
  const [error,           setError]           = useState(null);

  useEffect(() => {
    (async () => {
      const [fwRes, asmtRes] = await Promise.allSettled([api.frameworks.list(), api.assessments.list()]);
      const fws   = fwRes.status   === "fulfilled" ? fwRes.value   : [];
      const asmts = asmtRes.status === "fulfilled" ? asmtRes.value : [];
      if (fwRes.status !== "fulfilled" && asmtRes.status !== "fulfilled")
        setError("Cannot reach backend. Ensure the server is running on port 8000.");
      setFrameworks(fws);
      setAllAssessments(asmts);
      const done = asmts.filter(a => a.status === "completed").slice(0, 20);
      if (done.length > 0) {
        const sets = await Promise.all(done.map(a => api.assessments.findings(a.id).catch(() => [])));
        const map = {};
        done.forEach((a, i) => { map[a.id] = sets[i].filter(f => !f.status || f.status === "open"); });
        setFindingsMap(map);
      }
    })().catch(e => setError(e.message)).finally(() => setLoading(false));
  }, []);

  const assessments     = useMemo(() => allAssessments, [allAssessments]);
  const draft           = useMemo(() => assessments.filter(a => a.status === "draft"),       [assessments]);
  const inProgress      = useMemo(() => assessments.filter(a => a.status === "in_progress"), [assessments]);
  const completed       = useMemo(() => assessments.filter(a => a.status === "completed"),   [assessments]);
  const scopedFindings  = useMemo(() => {
    const ids = new Set(completed.map(a => a.id));
    return Object.entries(findingsMap).filter(([id]) => ids.has(id)).flatMap(([, fs]) => fs);
  }, [findingsMap, completed]);

  const organizations = new Set(assessments.map(a => a.organization).filter(Boolean)).size;
  const scored     = completed.filter(a => a.overall_score != null);
  const avgScore   = scored.length > 0 ? Math.round(scored.reduce((s, a) => s + a.overall_score, 0) / scored.length) : null;
  const matScored  = scored.filter(a => a.maturity_level != null);
  const avgMaturity = matScored.length > 0 ? Math.round(matScored.reduce((s, a) => s + a.maturity_level, 0) / matScored.length) : null;
  const critical   = scopedFindings.filter(f => f.severity === "critical");

  const kpis = [
    // Every KPI navigates somewhere. Targets must be keys that exist in both
    // AppShell's switch and auth.NAV_ACCESS — navigate() drops anything else
    // silently, which is why "frameworks" used to do nothing at all.
    { label: "Organizations", value: organizations,        icon: Building2,     accent: "bg-slate-800",   sub: "Organizations in scope", onClick: () => setActiveView("overall_report") },
    { label: "Assessments",  value: assessments.length,   icon: ClipboardCheck,accent: "bg-blue-700",   sub: `${completed.length} completed`,       onClick: () => setActiveView("assessments") },
    { label: "In Progress",  value: inProgress.length,    icon: BarChart3,     accent: "bg-amber-600",   sub: `${draft.length} draft`,               onClick: () => setActiveView("assessments") },
    { label: "Submitted",    value: completed.length,     icon: CheckCircle2,  accent: "bg-emerald-600", sub: "Scored",                              onClick: () => setActiveView("reports") },
    { label: "Open Findings",value: scopedFindings.length,icon: AlertTriangle, accent: "bg-rose-600",    sub: `${critical.length} critical`,         onClick: () => setActiveView("reports") },
    { label: "Avg Score",    value: avgScore != null ? `${avgScore}%` : "—",   icon: Shield,  accent: "bg-violet-700", sub: "Completed assessments",   onClick: () => setActiveView("reports") },
    { label: "Avg Maturity", value: avgMaturity != null ? `L${avgMaturity}` : "—", icon: TrendingUp, accent: "bg-indigo-600", sub: "Maturity level",  onClick: () => setActiveView("reports") },
    { label: "Frameworks",   value: frameworks.length,    icon: BookOpen,      accent: "bg-cyan-700",    sub: `${frameworks.reduce((s,f) => s + (f.total_controls||0), 0)} controls`, onClick: () => setActiveView("knowledge_base") },
  ];

  const statusSegments = [
    { label: "Draft",       value: draft.length,      color: "#a89c8c" },
    { label: "In Progress", value: inProgress.length, color: "#ffc72c" },
    { label: "Completed",   value: completed.length,  color: "#16a34a" },
  ];
  const sevBarData = [
    { label: "Critical", value: critical.length,                                        color: "#c81e1e" },
    { label: "High",     value: scopedFindings.filter(f => f.severity === "high").length,   color: "#ec6a0d" },
    { label: "Medium",   value: scopedFindings.filter(f => f.severity === "medium").length, color: "#ffc72c" },
    { label: "Low",      value: scopedFindings.filter(f => f.severity === "low").length,    color: "#b68a5a" },
  ];
  const quickActions = (ROLE_ACTIONS[role] || []).filter(a => !a.view || canView(role, a.view) || a.view === "assessments");

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-5 fade-in-up">
      <div className="flex items-start justify-between">
        <div>
          <p className="eyebrow eyebrow-gold mb-2">Program Overview</p>
          <h2 className="display-lg text-slate-900 title-rule">Executive Dashboard</h2>
          <p className="text-[13.5px] text-slate-500 mt-3 font-medium">Program-wide compliance posture and risk overview.</p>
        </div>
        <div className="flex items-center gap-3">
          {canCreate(role, "assessment") && (
            <button onClick={() => setActiveView("assessments")} className="btn-primary"><Plus size={14} /> New Assessment</button>
          )}
        </div>
      </div>

      {error && (
        <div className="alert alert-danger">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span className="font-semibold">{error}</span>
        </div>
      )}

      <div className="grid grid-cols-4 gap-3">
        {kpis.slice(0, 4).map(k => <KpiCard key={k.label} loading={loading} {...k} />)}
      </div>
      <div className="grid grid-cols-4 gap-3">
        {kpis.slice(4).map(k => <KpiCard key={k.label} loading={loading || (k.label === "Open Findings" && !Object.keys(findingsMap).length)} {...k} />)}
      </div>

      <div className="grid grid-cols-5 gap-5">
        <div className="col-span-3 card overflow-hidden">
          <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100"
            style={{ background: "linear-gradient(180deg, #fffdf8, #fff)" }}>
            <h3 className="font-display text-[14px] font-extrabold text-slate-900 tracking-tight">Recent Assessments</h3>
            <button onClick={() => setActiveView("assessments")} className="group flex items-center gap-1.5 text-[11.5px] font-bold uppercase tracking-wider text-blue-600 hover:text-blue-700 transition-colors">View all <ArrowRight size={12} className="group-hover:translate-x-0.5 transition-transform" /></button>
          </div>
          {loading ? (
            <div className="p-5 space-y-3">{[1,2,3].map(i => <div key={i} className="flex gap-3 items-center"><span className="skeleton w-2 h-2 rounded-full" /><span className="skeleton flex-1 h-4 rounded" /><span className="skeleton w-16 h-4 rounded" /></div>)}</div>
          ) : assessments.length === 0 ? (
            <div className="empty-state m-4" style={{ padding: "40px 24px" }}>
              <span className="empty-icon"><ClipboardCheck size={24} /></span>
              <p className="empty-title">No assessments in scope</p>
              <p className="empty-body">Start a new assessment to begin tracking posture.</p>
            </div>
          ) : (
            <div className="divide-y divide-slate-50">
              {assessments.slice(0, 8).map(a => {
                const badge = a.status === "completed" ? "badge-success" : a.status === "in_progress" ? "badge-medium" : "badge-neutral";
                const dot   = a.status === "completed" ? "status-completed" : a.status === "in_progress" ? "status-in_progress" : "status-draft";
                return (
                  <div key={a.id} onClick={() => setActiveView("assessments")}
                    className="group flex items-center gap-3 px-5 py-3.5 hover:bg-amber-50/50 cursor-pointer transition-colors">
                    <span className={`status-dot shrink-0 ${dot}`} />
                    <div className="flex-1 min-w-0">
                      <p className="text-[13.5px] font-bold text-slate-900 truncate group-hover:text-blue-700 transition-colors">{a.name}</p>
                      {a.organization && <p className="text-[11px] text-slate-400 mt-0.5 font-semibold">{a.organization}</p>}
                    </div>
                    <span className={`badge shrink-0 ${badge}`}>{a.status.replace("_", " ")}</span>
                    <span className="font-display text-sm font-extrabold text-slate-800 shrink-0 tabular-nums w-11 text-right">{(a.status === "in_review" || a.status === "completed") && a.overall_score != null ? `${a.overall_score}%` : "—"}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="col-span-2 space-y-4">
          <div className="card p-5">
            <p className="eyebrow mb-4">Program Health</p>
            {loading ? <div className="skeleton h-24 rounded-xl" /> : assessments.length === 0 ? (
              <p className="text-xs text-slate-400 text-center py-4">No data in scope.</p>
            ) : (
              <div className="flex items-center gap-4">
                <DonutChart segments={statusSegments} size={88} thickness={12}>
                  <span className="font-display text-lg font-extrabold text-slate-900 tabular-nums">{assessments.length}</span>
                </DonutChart>
                <div className="space-y-2">
                  {statusSegments.map(s => (
                    <div key={s.label} className="flex items-center gap-2">
                      <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: s.color }} />
                      <span className="text-[12px] text-slate-500 flex-1 font-semibold">{s.label}</span>
                      <span className="font-display text-[12.5px] font-extrabold text-slate-800 tabular-nums">{s.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
          <div className="card p-5">
            <p className="eyebrow mb-4">Findings by Severity</p>
            {loading ? <div className="skeleton h-20 rounded-xl" /> : scopedFindings.length === 0 ? (
              <p className="text-xs text-slate-400 text-center py-4">No open findings.</p>
            ) : <BarChart data={sevBarData} height={80} />}
          </div>
          <div className="card overflow-hidden">
            <div className="px-5 py-3.5 border-b border-slate-100 flex items-center justify-between"
              style={{ background: "linear-gradient(180deg, #fffdf8, #fff)" }}>
              <h3 className="font-display text-[13px] font-extrabold text-slate-900 tracking-tight">Critical Findings</h3>
              <span className={`badge ${critical.length ? "badge-critical" : "badge-neutral"}`}>{critical.length}</span>
            </div>
            {loading ? <div className="p-4"><div className="skeleton h-10 rounded-xl" /></div> :
            critical.length === 0 ? <p className="px-5 py-4 text-xs text-slate-400">No critical findings.</p> : (
              <div className="divide-y divide-slate-50">
                {critical.slice(0, 3).map((f, i) => (
                  <div key={i} className="px-5 py-2.5">
                    <p className="text-xs font-semibold text-slate-800 truncate">{f.control_code || f.domain_code || "Finding"}</p>
                    <p className="text-[10px] text-slate-400 truncate mt-0.5">{f.description}</p>
                  </div>
                ))}
                {critical.length > 3 && (
                  <button onClick={() => setActiveView("reports")} className="px-5 py-2.5 text-[10px] text-blue-600 hover:underline w-full text-left">
                    +{critical.length - 3} more in Reports →
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {quickActions.length > 0 && (
        <div>
          <p className="eyebrow mb-3">Quick Actions</p>
          <div className="flex flex-wrap gap-2.5">
            {quickActions.map(({ label, icon: Icon, view }) => (
              <button key={view} onClick={() => setActiveView(view)} className="btn-secondary btn-lg">
                <Icon size={14} />{label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
