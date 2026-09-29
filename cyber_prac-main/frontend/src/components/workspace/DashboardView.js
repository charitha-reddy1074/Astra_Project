"use client";

import { useEffect, useState, useMemo } from "react";
import {
  ClipboardCheck, BookOpen, AlertTriangle, BarChart3, Plus, ArrowRight,
  Inbox, Building2, Shield, TrendingUp, CheckCircle2, Globe, FileText,
  ArrowUpRight, Layers,
} from "lucide-react";
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

const STATUS_BADGE = {
  completed:   "badge-success",
  in_progress: "badge-medium",
  draft:       "badge-neutral",
};

function KpiCard({ label, value, sub, icon: Icon, accent, loading, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-accent={accent}
      className="kpi"
      aria-label={`${label}: ${value ?? "—"}`}
    >
      <div className="kpi-head">
        <span className="kpi-icon">
          <Icon size={16} strokeWidth={2} />
        </span>
        <span className="kpi-label truncate">{label}</span>
      </div>
      <div>
        <span className="kpi-value block">
          {loading || value == null
            ? <span className="skeleton inline-block h-7 w-12 align-middle" />
            : typeof value === "number" ? <CountUp to={value} /> : value}
        </span>
        {sub && <span className="kpi-sub block truncate">{sub}</span>}
      </div>
    </button>
  );
}

/* Shared legend row used by the donut + severity widgets. */
function Legend({ items, valueKey = "value" }) {
  return (
    <ul className="flex min-w-0 flex-1 flex-col gap-2.5">
      {items.map((s) => (
        <li key={s.label} className="flex items-center gap-2.5">
          <span
            className="h-2.5 w-2.5 shrink-0 rounded-full"
            style={{ background: s.color, boxShadow: `0 0 0 3px ${s.color}1F` }}
          />
          <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-slate-500">{s.label}</span>
          <span className="shrink-0 font-display text-[13px] font-bold tabular-nums text-slate-800">
            {s[valueKey]}
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function DashboardView({ setActiveView, role }) {
  const [frameworks,     setFrameworks]     = useState([]);
  const [allAssessments, setAllAssessments] = useState([]);
  const [findingsMap,    setFindingsMap]    = useState({});
  const [loading,        setLoading]        = useState(true);
  const [error,          setError]          = useState(null);

  useEffect(() => {
    (async () => {
      const [fwRes, asmtRes] = await Promise.allSettled([api.frameworks.list(), api.assessments.list()]);
      const fws   = fwRes.status   === "fulfilled" ? fwRes.value   : [];
      const asmts = asmtRes.status === "fulfilled" ? asmtRes.value : [];
      if (fwRes.status !== "fulfilled" && asmtRes.status !== "fulfilled")
        setError("Cannot reach backend. Ensure the server is running on port 8000.");
      setFrameworks(fws);
      setAllAssessments(asmts);
      const done = asmts.filter((a) => a.status === "completed").slice(0, 20);
      if (done.length > 0) {
        const sets = await Promise.all(done.map((a) => api.assessments.findings(a.id).catch(() => [])));
        const map = {};
        done.forEach((a, i) => { map[a.id] = sets[i].filter((f) => !f.status || f.status === "open"); });
        setFindingsMap(map);
      }
    })().catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, []);

  const assessments    = useMemo(() => allAssessments, [allAssessments]);
  const draft          = useMemo(() => assessments.filter((a) => a.status === "draft"),       [assessments]);
  const inProgress     = useMemo(() => assessments.filter((a) => a.status === "in_progress"), [assessments]);
  const completed      = useMemo(() => assessments.filter((a) => a.status === "completed"),   [assessments]);
  const scopedFindings = useMemo(() => {
    const ids = new Set(completed.map((a) => a.id));
    return Object.entries(findingsMap).filter(([id]) => ids.has(id)).flatMap(([, fs]) => fs);
  }, [findingsMap, completed]);

  const organizations = new Set(assessments.map((a) => a.organization).filter(Boolean)).size;
  const scored        = completed.filter((a) => a.overall_score != null);
  const avgScore      = scored.length > 0 ? Math.round(scored.reduce((s, a) => s + a.overall_score, 0) / scored.length) : null;
  const matScored     = scored.filter((a) => a.maturity_level != null);
  const avgMaturity   = matScored.length > 0 ? Math.round(matScored.reduce((s, a) => s + a.maturity_level, 0) / matScored.length) : null;
  const critical      = scopedFindings.filter((f) => f.severity === "critical");
  const high          = scopedFindings.filter((f) => f.severity === "high").length;
  const medium        = scopedFindings.filter((f) => f.severity === "medium").length;
  const low           = scopedFindings.filter((f) => f.severity === "low").length;
  const totalControls = frameworks.reduce((s, f) => s + (f.total_controls || 0), 0);

  // Every KPI navigates somewhere. Targets must be keys that exist in both
  // AppShell's switch and auth.NAV_ACCESS — navigate() drops anything else
  // silently, which is why "frameworks" used to do nothing at all.
  const kpis = [
    { label: "Organizations", value: organizations,           icon: Building2,     accent: "slate",   sub: "Organizations in scope", onClick: () => setActiveView("overall_report") },
    { label: "Assessments",   value: assessments.length,      icon: ClipboardCheck,accent: "accent",  sub: `${completed.length} completed`,      onClick: () => setActiveView("assessments") },
    { label: "In Progress",   value: inProgress.length,       icon: BarChart3,     accent: "amber",   sub: `${draft.length} draft`,              onClick: () => setActiveView("assessments") },
    { label: "Submitted",     value: completed.length,        icon: CheckCircle2,  accent: "emerald", sub: "Scored and reported",                onClick: () => setActiveView("reports") },
    { label: "Open Findings", value: scopedFindings.length,   icon: AlertTriangle, accent: "rose",    sub: `${critical.length} critical`,        onClick: () => setActiveView("reports") },
    { label: "Avg Score",     value: avgScore   != null ? `${avgScore}%` : "—",   icon: Shield,     accent: "violet", sub: "Across completed assessments",     onClick: () => setActiveView("reports") },
    { label: "Avg Maturity",  value: avgMaturity != null ? `L${avgMaturity}` : "—", icon: TrendingUp, accent: "plum",  sub: "Maturity level",                  onClick: () => setActiveView("reports") },
    { label: "Frameworks",    value: frameworks.length,       icon: BookOpen,      accent: "accent",  sub: `${totalControls.toLocaleString()} controls indexed`,    onClick: () => setActiveView("knowledge_base") },
  ];

  const statusSegments = [
    { label: "Draft",       value: draft.length,      color: "var(--chart-track)", border: "var(--border-medium)" },
    { label: "In Progress", value: inProgress.length, color: "var(--status-warning)" },
    { label: "Completed",   value: completed.length,  color: "var(--status-success)" },
  ];

  const sevBarData = [
    { label: "Critical", value: critical.length, color: "var(--status-danger)" },
    { label: "High",     value: high,            color: "var(--status-warning)" },
    { label: "Medium",   value: medium,          color: "var(--accent-soft)" },
    { label: "Low",      value: low,             color: "var(--chart-track)" },
  ];

  const quickActions = (ROLE_ACTIONS[role] || []).filter(
    (a) => !a.view || canView(role, a.view) || a.view === "assessments"
  );

  const completionRate = assessments.length > 0
    ? Math.round((completed.length / assessments.length) * 100)
    : null;

  return (
    <div className="dashboard-content fade-in-up">
      {/* ── Page header ─────────────────────────────────────────────────── */}
      <header className="mb-7 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <p className="eyebrow eyebrow-gold mb-2">Program Overview</p>
          <h2 className="page-title">Executive Dashboard</h2>
          <p className="mt-2.5 text-[13.5px] font-medium text-slate-500">
            Programme-wide compliance posture and risk overview.
          </p>
        </div>
        {canCreate(role, "assessment") && (
          <button onClick={() => setActiveView("assessments")} className="btn-primary shrink-0">
            <Plus size={14} /> New Assessment
          </button>
        )}
      </header>

      {error && (
        <div className="alert alert-danger mb-6">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span className="font-semibold">{error}</span>
        </div>
      )}

      {/* ── KPI row ─────────────────────────────────────────────────────── */}
      <section aria-label="Key performance indicators" className="dash-grid mb-7">
        {kpis.slice(0, 4).map((k) => (
          <div key={k.label} className="dash-col-3">
            <KpiCard loading={loading} {...k} />
          </div>
        ))}
      </section>

      <section aria-label="Coverage and risk indicators" className="dash-grid mb-8">
        {kpis.slice(4).map((k) => (
          <div key={k.label} className="dash-col-3">
            <KpiCard
              loading={loading || (k.label === "Open Findings" && !Object.keys(findingsMap).length)}
              {...k}
            />
          </div>
        ))}
      </section>

      {/* ── Main analytics ──────────────────────────────────────────────── */}
      <section aria-label="Recent assessments" className="dash-grid mb-8">
        {/* 8 cols — primary table */}
        <div className="dash-col-8 card card-sheen overflow-hidden">
          <div className="card-head">
            <h3 className="card-head-title">Recent Assessments</h3>
            <button
              onClick={() => setActiveView("assessments")}
              className="group flex shrink-0 items-center gap-1.5 text-[11.5px] font-semibold uppercase tracking-wider transition-colors"
              style={{ color: "var(--accent)" }}
            >
              View all
              <ArrowRight size={12} className="transition-transform group-hover:translate-x-0.5" />
            </button>
          </div>

          {loading ? (
            <div className="space-y-3 p-5">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="flex items-center gap-3">
                  <span className="skeleton h-2 w-2 shrink-0 rounded-full" />
                  <span className="skeleton h-4 flex-1 rounded" />
                  <span className="skeleton h-4 w-16 rounded" />
                </div>
              ))}
            </div>
          ) : assessments.length === 0 ? (
            <div className="empty-state m-4" style={{ padding: "48px 24px" }}>
              <span className="empty-icon"><ClipboardCheck size={22} /></span>
              <p className="empty-title">No assessments in scope</p>
              <p className="empty-body">Start a new assessment to begin tracking posture.</p>
            </div>
          ) : (
            <ul className="divide-y" style={{ borderColor: "var(--border-hairline)" }}>
              {assessments.slice(0, 8).map((a) => (
                <li key={a.id}>
                  <button
                    onClick={() => setActiveView("assessments")}
                    className="row-item group flex w-full items-center gap-3 px-[var(--card-padding)] py-3.5 text-left"
                  >
                    <span className={`status-dot shrink-0 ${
                      a.status === "completed" ? "status-completed"
                      : a.status === "in_progress" ? "status-in_progress"
                      : "status-draft"
                    }`} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13.5px] font-semibold text-slate-900 transition-colors group-hover:text-blue-700">
                        {a.name}
                      </span>
                      {a.organization && (
                        <span className="mt-0.5 block truncate text-[11px] font-medium text-slate-400">
                          {a.organization}
                        </span>
                      )}
                    </span>
                    <span className={`badge shrink-0 ${STATUS_BADGE[a.status] || "badge-neutral"}`}>
                      {String(a.status || "draft").replace("_", " ")}
                    </span>
                    <span className="tnum w-12 shrink-0 text-right font-display text-[13px] font-bold text-slate-800">
                      {(a.status === "in_review" || a.status === "completed") && a.overall_score != null
                        ? `${a.overall_score}%`
                        : "—"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* 4 cols — stacked analytics widgets */}
        <div className="dash-col-4 flex flex-col gap-[var(--card-gap)]">
          {/* Program health */}
          <div className="card">
            <div className="card-head">
              <h3 className="card-head-title">Program Health</h3>
              {completionRate != null && (
                <span className="badge badge-medium">{completionRate}% complete</span>
              )}
            </div>
            <div className="card-body">
              {loading ? (
                <div className="skeleton h-[104px] w-full rounded-xl" />
              ) : assessments.length === 0 ? (
                <p className="py-6 text-center text-xs text-slate-400">No data in scope.</p>
              ) : (
                <div className="flex items-center gap-5">
                  <DonutChart segments={statusSegments} size={104} thickness={14}>
                    <span className="font-display text-xl font-extrabold tabular-nums text-slate-900">
                      {assessments.length}
                    </span>
                    <span className="mt-0.5 text-[9px] font-semibold uppercase tracking-[0.1em] text-slate-400">
                      Total
                    </span>
                  </DonutChart>
                  <Legend items={statusSegments} />
                </div>
              )}
            </div>
          </div>

          {/* Findings by severity */}
          <div className="card">
            <div className="card-head">
              <h3 className="card-head-title">Findings by Severity</h3>
              <span className="badge badge-neutral">{scopedFindings.length} open</span>
            </div>
            <div className="card-body">
              {loading ? (
                <div className="skeleton h-[92px] w-full rounded-xl" />
              ) : scopedFindings.length === 0 ? (
                <p className="py-6 text-center text-xs text-slate-400">No open findings.</p>
              ) : (
                <BarChart data={sevBarData} height={96} />
              )}
            </div>
          </div>

          {/* Critical findings */}
          <div className="card overflow-hidden">
            <div className="card-head">
              <h3 className="card-head-title">Critical Findings</h3>
              <span className={`badge ${critical.length ? "badge-critical" : "badge-neutral"}`}>
                {critical.length}
              </span>
            </div>
            {loading ? (
              <div className="p-4"><div className="skeleton h-10 w-full rounded-xl" /></div>
            ) : critical.length === 0 ? (
              <p className="px-[var(--card-padding)] py-5 text-xs text-slate-400">No critical findings.</p>
            ) : (
              <ul className="divide-y" style={{ borderColor: "var(--border-hairline)" }}>
                {critical.slice(0, 3).map((f, i) => (
                  <li key={i} className="px-[var(--card-padding)] py-2.5">
                    <p className="truncate text-xs font-semibold text-slate-800">
                      {f.control_code || f.domain_code || "Finding"}
                    </p>
                    <p className="mt-0.5 truncate text-[10.5px] text-slate-400">{f.description}</p>
                  </li>
                ))}
              </ul>
            )}
            {critical.length > 3 && (
              <button
                onClick={() => setActiveView("reports")}
                className="flex w-full items-center gap-1.5 px-[var(--card-padding)] py-3 text-[11px] font-semibold row-item"
                style={{ color: "var(--accent)", borderTop: "1px solid var(--border-hairline)" }}
              >
                +{critical.length - 3} more in Reports <ArrowUpRight size={12} />
              </button>
            )}
          </div>
        </div>
      </section>

      {/* ── Supporting widgets ──────────────────────────────────────────── */}
      <section aria-label="Quick actions and framework coverage" className="dash-grid">
        <div className="dash-col-8 card">
          <div className="card-head">
            <h3 className="card-head-title">Quick Actions</h3>
            <Layers size={15} style={{ color: "var(--text-faint)" }} />
          </div>
          <div className="card-body">
            {quickActions.length === 0 ? (
              <p className="py-2 text-xs text-slate-400">No actions available for this role.</p>
            ) : (
              <div className="flex flex-wrap gap-2.5">
                {quickActions.map(({ label, icon: Icon, view }) => (
                  <button key={label} onClick={() => setActiveView(view)} className="btn-secondary btn-lg">
                    <Icon size={14} />{label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="dash-col-4 card card-sheen">
          <div className="card-head">
            <h3 className="card-head-title">Framework Library</h3>
            <button
              onClick={() => setActiveView("knowledge_base")}
              className="flex shrink-0 items-center gap-1 text-[11.5px] font-semibold transition-colors"
              style={{ color: "var(--accent)" }}
            >
              Browse <ArrowRight size={12} />
            </button>
          </div>
          <div className="card-body">
            {loading ? (
              <div className="skeleton h-16 w-full rounded-xl" />
            ) : frameworks.length === 0 ? (
              <p className="py-3 text-xs text-slate-400">No frameworks loaded.</p>
            ) : (
              <>
                <div className="flex items-baseline gap-2">
                  <span className="font-display text-[30px] font-extrabold leading-none tabular-nums text-slate-900">
                    {totalControls.toLocaleString()}
                  </span>
                  <span className="text-[11.5px] font-medium text-slate-400">controls indexed</span>
                </div>
                <p className="mt-2.5 line-clamp-2 text-[11.5px] leading-relaxed text-slate-500">
                  {frameworks.slice(0, 4).map((f) => f.name || f.code).join(" · ")}
                  {frameworks.length > 4 ? ` +${frameworks.length - 4} more` : ""}
                </p>
              </>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
