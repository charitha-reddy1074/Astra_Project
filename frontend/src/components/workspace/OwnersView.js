"use client";

// Admin → Users. A people-management console for the Organization Owner: headcount
// by role, the organization-coverage gap view, search/filter, contact actions
// (mailto / copy email / copy a whole cohort) and full profile management.
// Workload is derived client-side from /assessments and /document-requests.

import { useState, useEffect, useMemo, useCallback } from "react";
import {
  Users, UserPlus, RefreshCw, Search, Mail, MapPin, Shield, Copy, Check,
  AlertTriangle, Download, Trash2, Pencil, Power, X, ChevronDown,
  ShieldAlert, CheckCircle2, Globe, ClipboardCheck, FileText, Phone,
} from "lucide-react";
import { api } from "@/lib/api";
import { CountUp, DonutChart } from "@/components/ui/Charts";
import {
  ASSIGNABLE_ROLES, ROLE_META, roleLabel, organizationKey, organizationLabelOf,
  getOrganizationList, buildCoverage, computeWorkload, primaryWorkload, initials, avatarTint,
  copyText, mailtoUrl, usersToCsv, downloadCsv,
  syncUsersFromApi, apiCreateUser, apiUpdateUser, apiDeleteUser, CONTRIBUTOR_ROLES,
} from "@/lib/users";
import { UserFormModal, ConfirmDialog, UserDetailDrawer, Portal } from "@/components/workspace/UserModals";

// Filter-bar select: same shell as the search box in AssessmentsView (white card,
// slate-200 border, rounded-xl, blue focus ring) with room for the chevron.
const SELECT_CLS =
  "text-sm border border-slate-200 rounded-xl pl-4 pr-8 py-2.5 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-50 transition-all text-slate-700 bg-white appearance-none cursor-pointer";

const REVIEWER_KEYS = ["org_owner", "compliance_manager", "security_manager", "auditor"];

const SORTS = {  name:          (a, b) => (a.name || "").localeCompare(b.name || ""),
  role:          (a, b) => (a.role || "").localeCompare(b.role || "") || (a.name || "").localeCompare(b.name || ""),
  organization:  (a, b) => (a._orgLabel || "zzz").localeCompare(b._orgLabel || "zzz") || (a.name || "").localeCompare(b.name || ""),
};

// ── Small pieces ─────────────────────────────────────────────────────────────

// Identical to DashboardView's KpiCard — same padding, label casing, value size
// and icon-tile treatment, so the two screens read as one system.
function KpiCard({ label, value, sub, icon: Icon, accent, loading, onClick }) {
  return (
    <div onClick={onClick}
      className={`bg-white rounded-2xl border border-slate-100 p-4 transition-all ${onClick ? "cursor-pointer hover:border-blue-200 hover:shadow-md" : ""}`}
      style={{ boxShadow: "var(--shadow-card)" }}>
      <div className="flex items-start justify-between mb-2.5">
        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider leading-tight">{label}</p>
        <div className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 ${accent}`}>
          <Icon size={12} className="text-white" />
        </div>
      </div>
      <p className="text-2xl font-bold text-slate-900 tabular-nums tracking-tight">
        {loading || value == null ? <span className="skeleton inline-block w-10 h-7 rounded" />
          : typeof value === "number" ? <CountUp to={value} /> : value}
      </p>
      {sub && <p className="text-[10px] text-slate-400 mt-1">{sub}</p>}
    </div>
  );
}

function CopyButton({ value, label, title, className = "" }) {
  const [done, setDone] = useState(false);
  const click = async (e) => {
    e.stopPropagation();
    if (await copyText(value)) { setDone(true); setTimeout(() => setDone(false), 1400); }
  };
  if (!value) return null;
  return (
    <button type="button" onClick={click} title={title}
      className={`inline-flex items-center gap-1 transition-colors ${className}`}>
      {done ? <Check size={12} className="text-emerald-600" /> : <Copy size={12} />}
      {label && <span>{done ? "Copied" : label}</span>}
    </button>
  );
}

function Toast({ message, onDone }) {
  useEffect(() => {
    if (!message) return;
    const t = setTimeout(onDone, 2200);
    return () => clearTimeout(t);
  }, [message, onDone]);
  if (!message) return null;
  return (
    <Portal>
      <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[60] flex items-center gap-2 px-4 py-2.5 rounded-xl bg-white border border-slate-200 text-xs font-semibold text-slate-700"
        style={{ boxShadow: "var(--shadow-elevated)" }}>
        <CheckCircle2 size={14} className="text-emerald-600" /> {message}
      </div>
    </Portal>
  );
}

function Select({ value, onChange, children, width = "" }) {
  return (
    <div className={`relative ${width}`}>
      <select value={value} onChange={onChange} className={`${SELECT_CLS} w-full`}>{children}</select>
      <ChevronDown size={13} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
    </div>
  );
}

// ── Main ─────────────────────────────────────────────────────────────────────

export default function OwnersView() {
  const [users,        setUsers]        = useState([]);
  const [assessments,  setAssessments]  = useState([]);
  const [docRequests,  setDocRequests]  = useState([]);
  const [loading,      setLoading]      = useState(true);
  const [error,        setError]        = useState(null);
  const [toast,        setToast]        = useState(null);

  // filters
  const [q,           setQ]           = useState("");
  const [roleFilter,  setRoleFilter]  = useState("all");
  const [orgFilter,   setOrgFilter]   = useState("all");
  const [statusFilter,setStatusFilter]= useState("all");
  const [sortBy,      setSortBy]      = useState("name");

  // dialogs
  const [formOpen, setFormOpen] = useState(false);
  const [editing,  setEditing]  = useState(null);
  const [detail,   setDetail]   = useState(null);   // email of the person in the drawer
  const [confirm,  setConfirm]  = useState(null);   // { title, message, confirmLabel, tone, run }
  const [confirmBusy, setConfirmBusy] = useState(false);

  const organizations = useMemo(() => getOrganizationList(), []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const [uRes, aRes, dRes] = await Promise.allSettled([
      api.users.list(),
      api.assessments.list(),
      api.documentRequests.listAll(),
    ]);
    if (uRes.status === "fulfilled") {
      setUsers(uRes.value || []);
      syncUsersFromApi();
    } else {
      setError("Could not load the user directory. Is the backend running on port 8000?");
    }
    // Workload sources are best-effort — the directory still works without them.
    setAssessments(aRes.status === "fulfilled" ? (aRes.value || []) : []);
    setDocRequests(dRes.status === "fulfilled" ? (dRes.value || []) : []);
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  // Decorate once so filtering/sorting/CSV all share the same resolved labels.
  const decorated = useMemo(() => users.map((u) => ({
    ...u,
    _orgLabel: organizationLabelOf(u.organization, organizations),
    _orgKey:   organizationKey(organizationLabelOf(u.organization, organizations) || u.organization),
    _active:   u.is_active !== false,
  })), [users, organizations]);

  const workload = useMemo(
    () => computeWorkload(users, assessments, docRequests, organizations),
    [users, assessments, docRequests, organizations]
  );
  const coverage = useMemo(() => buildCoverage(decorated, organizations), [decorated, organizations]);

  const counts = useMemo(() => {
    const by = {};
    decorated.forEach((u) => { by[u.role] = (by[u.role] || 0) + 1; });
    return {
      total:    decorated.length,
      active:   decorated.filter((u) => u._active).length,
      inactive: decorated.filter((u) => !u._active).length,
      by,
    };
  }, [decorated]);

  const gaps = useMemo(() => ({
    noContributor: coverage.filter((c) => !c.hasContributor),
    noReviewer:    coverage.filter((c) => !c.hasReviewer),
    covered:       coverage.filter((c) => !c.gap),
  }), [coverage]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return decorated
      .filter((u) => {
        if (roleFilter !== "all" && u.role !== roleFilter) return false;
        if (statusFilter !== "all" && (statusFilter === "active") !== u._active) return false;
        if (orgFilter !== "all") {
          if (orgFilter === "none") { if (u.organization) return false; }
          else if (u._orgKey !== organizationKey(orgFilter)) return false;
        }
        if (!needle) return true;
        return [u.name, u.email, u.job_title, u.department, u._orgLabel, roleLabel(u.role)]
          .some((v) => (v || "").toLowerCase().includes(needle));
      })
      .sort(SORTS[sortBy] || SORTS.name);
  }, [decorated, q, roleFilter, orgFilter, statusFilter, sortBy]);

  const filtersActive = q || roleFilter !== "all" || orgFilter !== "all" || statusFilter !== "all";
  const detailUser = detail ? decorated.find((u) => u.email === detail) : null;

  // ── Actions ────────────────────────────────────────────────────────────────

  const flash = useCallback((m) => setToast(m), []);

  const copyList = useCallback(async (list, what) => {
    const emails = list.map((u) => u.email).filter(Boolean);
    if (emails.length === 0) return flash("No addresses to copy.");
    if (await copyText(emails.join("; "))) flash(`Copied ${emails.length} ${what || "address"}${emails.length === 1 ? "" : "es"}.`);
    else flash("Clipboard unavailable in this browser.");
  }, [flash]);

  const openCreate = () => { setEditing(null); setFormOpen(true); };
  const openEdit   = (u) => { setEditing(u); setFormOpen(true); };

  const handleSubmit = async (values) => {
    if (editing) {
      await apiUpdateUser(editing.email, {
        name: values.name, role: values.role, organization: values.organization,
        phone: values.phone, department: values.department,
        job_title: values.job_title, notes: values.notes, is_active: values.is_active,
      });
      flash(`${values.name} updated.`);
    } else {
      await apiCreateUser(values);
      flash(`${values.name} added to the directory.`);
    }
    setFormOpen(false);
    setEditing(null);
    await load();
  };

  const runConfirm = async () => {
    if (!confirm) return;
    setConfirmBusy(true);
    try {
      await confirm.run();
      setConfirm(null);
      await load();
    } catch (e) {
      setError(e?.message || "Action failed.");
      setConfirm(null);
    } finally {
      setConfirmBusy(false);
    }
  };

  const askDelete = (u) => setConfirm({
    title: `Delete ${u.name}?`,
    message: `${u.email} will be permanently removed and will lose access immediately. Their assessment history is not deleted. This cannot be undone.`,
    confirmLabel: "Delete person",
    tone: "danger",
    run: async () => { await apiDeleteUser(u.email); setDetail(null); flash(`${u.name} deleted.`); },
  });

  const askToggleActive = (u) => {
    const active = u.is_active !== false;
    setConfirm({
      title: active ? `Deactivate ${u.name}?` : `Reactivate ${u.name}?`,
      message: active
        ? `${u.email} keeps their record and history but is treated as inactive across the platform.`
        : `${u.email} will be marked active again.`,
      confirmLabel: active ? "Deactivate" : "Reactivate",
      tone: active ? "warn" : "warn",
      run: async () => {
        await apiUpdateUser(u.email, { is_active: !active });
        flash(active ? `${u.name} deactivated.` : `${u.name} reactivated.`);
      },
    });
  };

  const exportCsv = () => {
    if (filtered.length === 0) return flash("Nothing to export.");
    const csv = usersToCsv(filtered, [
      { header: "Name",       value: (u) => u.name },
      { header: "Email",      value: (u) => u.email },
      { header: "Role",       value: (u) => roleLabel(u.role) },
      { header: "Organization", value: (u) => u._orgLabel || "" },
      { header: "Job title",  value: (u) => u.job_title || "" },
      { header: "Department", value: (u) => u.department || "" },
      { header: "Phone",      value: (u) => u.phone || "" },
      { header: "Status",     value: (u) => (u._active ? "Active" : "Inactive") },
      { header: "Assessments",value: (u) => workload[(u.email || "").toLowerCase()]?.assessments ?? 0 },
      { header: "Doc requests",value:(u) => workload[(u.email || "").toLowerCase()]?.docRequests ?? 0 },
    ]);
    downloadCsv(`cyberai-people-${new Date().toISOString().slice(0, 10)}.csv`, csv);
    flash(`Exported ${filtered.length} ${filtered.length === 1 ? "person" : "people"}.`);
  };

  const clearFilters = () => { setQ(""); setRoleFilter("all"); setOrgFilter("all"); setStatusFilter("all"); };

  // ── Render ─────────────────────────────────────────────────────────────────

  const kpis = [
    { id: "all",           label: "Total People",   value: counts.total, icon: Users, accent: "bg-slate-800", sub: `${counts.active} active · ${counts.inactive} inactive` },
    { id: "contributors",  label: "Contributors",   value: (counts.by.team_member || 0) + (counts.by.evidence_contributor || 0), icon: Shield, accent: "bg-violet-700", sub: `${gaps.noContributor.length} organization${gaps.noContributor.length === 1 ? "" : "s"} uncovered` },
    { id: "reviewers",     label: "Reviewers",      value: REVIEWER_KEYS.reduce((n, k) => n + (counts.by[k] || 0), 0), icon: ClipboardCheck, accent: "bg-indigo-600", sub: `${gaps.noReviewer.length} organization${gaps.noReviewer.length === 1 ? "" : "s"} uncovered` },
    { id: "managers",      label: "Managers",       value: (counts.by.org_owner || 0) + (counts.by.compliance_manager || 0) + (counts.by.security_manager || 0), icon: Globe, accent: "bg-blue-700", sub: "Operate across every organization" },
    { id: "gaps",          label: "Coverage Gaps",  value: coverage.filter((c) => c.gap).length, icon: ShieldAlert, accent: coverage.some((c) => c.gap) ? "bg-rose-600" : "bg-emerald-600", sub: `of ${organizations.length} organizations` },
  ];

  const roleSegments = [
    { label: "Contributors", value: (counts.by.team_member || 0) + (counts.by.evidence_contributor || 0), color: ROLE_META.evidence_contributor.color },
    { label: "Auditors",     value: counts.by.auditor || 0,            color: ROLE_META.auditor.color },
    { label: "Managers",     value: (counts.by.org_owner || 0) + (counts.by.compliance_manager || 0) + (counts.by.security_manager || 0), color: ROLE_META.compliance_manager.color },
  ];

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-5 fade-in-up">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 tracking-tight">People Directory</h2>
          <p className="text-sm text-slate-500 mt-1">
            Assessors and market owners across the programme — coverage, contact details and workload.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={load} disabled={loading} className="btn-secondary text-xs flex items-center gap-1.5">
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} /> Refresh
          </button>
          <button onClick={exportCsv} disabled={loading} className="btn-secondary text-xs flex items-center gap-1.5">
            <Download size={13} /> Export
          </button>
          <button onClick={openCreate} className="btn-primary"><UserPlus size={14} /> Add person</button>
        </div>
      </div>

      {error && (
        <div className="flex items-start gap-3 px-4 py-3 rounded-xl border bg-rose-50 border-rose-200 text-rose-700 text-sm">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <button onClick={load} className="text-xs font-bold underline shrink-0">Retry</button>
        </div>
      )}

      {/* KPIs — each one also drives the directory filter below. */}
      <div className="grid grid-cols-5 gap-3">
        {kpis.map(({ id, ...k }) => (
          <KpiCard key={id} loading={loading} {...k}
            onClick={id === "gaps"
              ? () => document.getElementById("coverage-panel")?.scrollIntoView({ behavior: "smooth", block: "center" })
              : () => setRoleFilter(id)} />
        ))}
      </div>

      {/* Coverage + role split */}
      <div className="grid grid-cols-5 gap-5">
        <div id="coverage-panel" className="col-span-3 bg-white rounded-2xl border border-slate-100 overflow-hidden"
          style={{ boxShadow: "var(--shadow-card)" }}>
          <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
            <div>
              <h3 className="text-sm font-bold text-slate-900">Organization Coverage</h3>
              <p className="text-[11px] text-slate-400 mt-0.5">Who is accountable in each organization — and where nobody is.</p>
            </div>
            <span className={`badge ${coverage.some((c) => c.gap) ? "badge-high" : "badge-success"}`}>
              {gaps.covered.length}/{organizations.length} fully covered
            </span>
          </div>
          {loading ? (
            <div className="p-5 space-y-2.5">{[1, 2, 3, 4].map((i) => <div key={i} className="skeleton h-8 rounded-lg" />)}</div>
          ) : organizations.length === 0 ? (
            <div className="py-12 text-center">
              <Globe size={24} className="text-slate-200 mx-auto mb-3" />
              <p className="text-sm font-medium text-slate-500">No organizations defined</p>
              <p className="text-xs text-slate-400 mt-1">Assign an organization to a person in Users to see it here.</p>
            </div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50/60">
                  <th className="text-left px-5 py-2.5 text-[10px] font-bold text-slate-400 uppercase tracking-wider">Organization</th>
                  <th className="text-left px-3 py-2.5 text-[10px] font-bold text-slate-400 uppercase tracking-wider">Contributor</th>
                  <th className="text-left px-3 py-2.5 text-[10px] font-bold text-slate-400 uppercase tracking-wider">Reviewer</th>
                  <th className="text-right px-5 py-2.5 text-[10px] font-bold text-slate-400 uppercase tracking-wider w-20">Status</th>
                </tr>
              </thead>
              <tbody>
                {coverage.map((c) => (
                  <tr key={c.id}
                    onClick={() => { setOrgFilter(c.id); setRoleFilter("all"); document.getElementById("directory-panel")?.scrollIntoView({ behavior: "smooth", block: "start" }); }}
                    className="border-b border-slate-50 last:border-0 hover:bg-slate-50/60 cursor-pointer transition-colors">
                    <td className="px-5 py-2.5">
                      <p className="text-xs font-semibold text-slate-800">{c.label}</p>
                    </td>
                    <td className="px-3 py-2.5">
                      {c.contributors.length > 0 ? (
                        <span className="text-xs text-slate-600 truncate">
                          {c.contributors[0].name}{c.contributors.length > 1 && <span className="text-slate-400"> +{c.contributors.length - 1}</span>}
                        </span>
                      ) : <span className="badge badge-critical">No contributor</span>}
                    </td>
                    <td className="px-3 py-2.5">
                      {c.reviewers.length > 0 ? (
                        <span className="text-xs text-slate-600 truncate">
                          {c.reviewers[0].name}{c.reviewers.length > 1 && <span className="text-slate-400"> +{c.reviewers.length - 1}</span>}
                        </span>
                      ) : <span className="badge badge-medium">No reviewer</span>}
                    </td>
                    <td className="px-5 py-2.5 text-right">
                      <span className={`badge ${c.gap ? (c.hasContributor ? "badge-medium" : "badge-critical") : "badge-success"}`}>
                        {c.gap ? "Gap" : "Covered"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="col-span-2 space-y-4">
          <div className="bg-white rounded-2xl border border-slate-100 p-5" style={{ boxShadow: "var(--shadow-card)" }}>
            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-4">Directory by Role</p>
            {loading ? <div className="skeleton h-24 rounded-xl" /> : counts.total === 0 ? (
              <p className="text-xs text-slate-400 text-center py-4">No people yet.</p>
            ) : (
              <div className="flex items-center gap-4">
                <DonutChart segments={roleSegments} size={88} thickness={12}>
                  <span className="text-base font-bold text-slate-900 tabular-nums">{counts.total}</span>
                </DonutChart>
                <div className="space-y-1.5 flex-1 min-w-0">
                  {roleSegments.map((s) => (
                    <div key={s.label} className="flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full shrink-0" style={{ background: s.color }} />
                      <span className="text-xs text-slate-500 flex-1 truncate">{s.label}</span>
                      <span className="text-xs font-bold text-slate-700 tabular-nums">{s.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="bg-white rounded-2xl border border-slate-100 overflow-hidden" style={{ boxShadow: "var(--shadow-card)" }}>
            <div className="px-5 py-3 border-b border-slate-100">
              <h3 className="text-xs font-bold text-slate-900">Contact a cohort</h3>
              <p className="text-[10px] text-slate-400 mt-0.5">Email or copy every address in a group.</p>
            </div>
            <div className="divide-y divide-slate-50">
              {ASSIGNABLE_ROLES.map((r) => {
                const cohort = decorated.filter((u) => u.role === r.key && u._active);
                return (
                  <div key={r.key} className="flex items-center gap-2 px-5 py-2.5">
                    <span className="w-2 h-2 rounded-full shrink-0" style={{ background: ROLE_META[r.key]?.color }} />
                    <span className="text-xs text-slate-600 flex-1 truncate">{r.label}s</span>
                    <span className="text-xs font-bold text-slate-700 tabular-nums w-5 text-right">{cohort.length}</span>
                    <a href={mailtoUrl(cohort.map((u) => u.email), `Cyber Assessment Agent — message to all ${r.label}s`) || undefined}
                      onClick={(e) => { if (cohort.length === 0) e.preventDefault(); }}
                      title={`Email all ${r.label}s`}
                      className={`p-1 rounded-md transition-colors ${cohort.length ? "text-slate-300 hover:text-blue-600 hover:bg-blue-50" : "text-slate-200 cursor-not-allowed"}`}>
                      <Mail size={13} />
                    </a>
                    <button onClick={() => copyList(cohort, "address")} title={`Copy all ${r.label} emails`}
                      className={`p-1 rounded-md transition-colors ${cohort.length ? "text-slate-300 hover:text-blue-600 hover:bg-blue-50" : "text-slate-200 cursor-not-allowed"}`}>
                      <Copy size={13} />
                    </button>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="bg-white rounded-2xl border border-slate-100 overflow-hidden" style={{ boxShadow: "var(--shadow-card)" }}>
            <div className="px-5 py-3 border-b border-slate-100 flex items-center justify-between">
              <h3 className="text-xs font-bold text-slate-900">Needs Attention</h3>
              <span className={`badge ${coverage.some((c) => c.gap) ? "badge-high" : "badge-success"}`}>
                {coverage.filter((c) => c.gap).length}
              </span>
            </div>
            {loading ? (
              <div className="p-4"><div className="skeleton h-10 rounded-xl" /></div>
            ) : !coverage.some((c) => c.gap) ? (
              <div className="px-5 py-5 text-center">
                <CheckCircle2 size={20} className="text-emerald-500 mx-auto mb-2" />
                <p className="text-xs text-slate-500">Every organization has a reviewer and a contributor.</p>
              </div>
            ) : (
              <div className="divide-y divide-slate-50">
                {coverage.filter((c) => c.gap).slice(0, 4).map((c) => (
                  <button key={c.id} onClick={() => { setOrgFilter(c.id); setRoleFilter("all"); setEditing(null); setFormOpen(true); }}
                    className="w-full text-left px-5 py-2.5 hover:bg-slate-50 transition-colors flex items-center gap-2">
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-semibold text-slate-800 truncate">{c.label}</p>
                      <p className="text-[10px] text-slate-400">
                        Missing {[!c.hasContributor && "a contributor", !c.hasReviewer && "a reviewer"].filter(Boolean).join(" and ")}
                      </p>
                    </div>
                    <span className="text-[10px] font-bold text-blue-600 shrink-0 inline-flex items-center gap-1">
                      <UserPlus size={11} /> Add
                    </span>
                  </button>
                ))}
                {coverage.filter((c) => c.gap).length > 4 && (
                  <p className="px-5 py-2 text-[10px] text-slate-400">
                    +{coverage.filter((c) => c.gap).length - 4} more organization{coverage.filter((c) => c.gap).length - 4 === 1 ? "" : "s"} with gaps
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Directory */}
      <div id="directory-panel">
        {/* Search + filter row — same treatment as AssessmentsView. */}
        <div className="flex items-center gap-3 flex-wrap mb-4">
          <div className="flex items-center gap-2 bg-white border border-slate-200 rounded-xl px-4 py-2.5 flex-1 max-w-sm focus-within:border-blue-400 focus-within:ring-2 focus-within:ring-blue-50 transition-all">
            <Search size={14} className="text-slate-400 shrink-0" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name, email, organization or title…"
              className="flex-1 min-w-0 text-sm text-slate-700 outline-none placeholder:text-slate-400 bg-transparent" />
            {q && <button onClick={() => setQ("")} className="text-slate-400 hover:text-slate-600"><X size={12} /></button>}
          </div>
          <Select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}>
            <option value="all">All roles</option>
            {ASSIGNABLE_ROLES.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
          </Select>
          <Select value={orgFilter} onChange={(e) => setOrgFilter(e.target.value)}>
            <option value="all">All organizations</option>
            <option value="none">No organization</option>
            {organizations.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </Select>
          <Select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="all">Any status</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </Select>
          <Select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
            <option value="name">Sort: Name</option>
            <option value="role">Sort: Role</option>
            <option value="organization">Sort: Organization</option>
          </Select>
          {filtersActive && (
            <button onClick={clearFilters} className="text-xs font-semibold text-blue-600 hover:text-blue-700 px-2 py-2.5">Clear</button>
          )}
        </div>

        <div className="flex items-center justify-between mb-3">
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">
            {loading ? "Loading…" : `${filtered.length} of ${counts.total} ${counts.total === 1 ? "person" : "people"}`}
          </p>
          {filtered.length > 0 && (
            <CopyButton value={filtered.map((u) => u.email).join("; ")} label={`Copy ${filtered.length} email${filtered.length === 1 ? "" : "s"}`}
              title="Copy every address in this view"
              className="text-[11px] font-semibold text-slate-500 hover:text-blue-600" />
          )}
        </div>

        {loading ? (
          <div className="space-y-2.5">{[1, 2, 3, 4, 5].map((i) => <div key={i} className="skeleton h-16 rounded-2xl" />)}</div>
        ) : counts.total === 0 ? (
          <div className="bg-white border border-slate-100 rounded-2xl py-16 text-center" style={{ boxShadow: "var(--shadow-card)" }}>
            <Users size={28} className="text-slate-200 mx-auto mb-3" />
            <p className="text-sm font-semibold text-slate-500 mb-1">No people yet</p>
            <p className="text-xs text-slate-400 mb-4">Add reviewers and contributors to build the directory.</p>
            <button onClick={openCreate} className="btn-primary mx-auto"><UserPlus size={14} /> Add the first person</button>
          </div>
        ) : filtered.length === 0 ? (
          <div className="bg-white border border-slate-100 rounded-2xl py-16 text-center" style={{ boxShadow: "var(--shadow-card)" }}>
            <Search size={26} className="text-slate-200 mx-auto mb-3" />
            <p className="text-sm font-semibold text-slate-500 mb-1">No matches</p>
            <p className="text-xs text-slate-400 mb-4">Nobody matches the current search and filters.</p>
            <button onClick={clearFilters} className="btn-secondary text-xs mx-auto">Clear filters</button>
          </div>
        ) : (
          <div className="bg-white border border-slate-100 rounded-2xl overflow-hidden" style={{ boxShadow: "var(--shadow-card)" }}>
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50/60">
                  <th className="text-left px-5 py-3 text-[11px] font-bold text-slate-400 uppercase tracking-wider">Person</th>
                  <th className="text-left px-4 py-3 text-[11px] font-bold text-slate-400 uppercase tracking-wider">Contact</th>
                  <th className="text-left px-4 py-3 text-[11px] font-bold text-slate-400 uppercase tracking-wider">Access</th>
                  <th className="text-left px-4 py-3 text-[11px] font-bold text-slate-400 uppercase tracking-wider">Organization</th>
                  <th className="text-left px-4 py-3 text-[11px] font-bold text-slate-400 uppercase tracking-wider">Workload</th>
                  <th className="text-right px-5 py-3 text-[11px] font-bold text-slate-400 uppercase tracking-wider w-28">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((u) => {
                  const meta = ROLE_META[u.role] || {};
                  const wl   = primaryWorkload(u, workload);
                  const isContributor = CONTRIBUTOR_ROLES.includes(u.role);
                  return (
                    <tr key={u.email} onClick={() => setDetail(u.email)}
                      className={`border-b border-slate-50 last:border-0 hover:bg-slate-50/60 transition-colors cursor-pointer ${!u._active ? "opacity-60" : ""}`}>
                      <td className="px-5 py-3.5">
                        <div className="flex items-center gap-3">
                          <div className={`w-8 h-8 rounded-xl flex items-center justify-center text-[11px] font-bold shrink-0 ${avatarTint(u.email)}`}>
                            {initials(u.name, u.email)}
                          </div>
                          <div className="min-w-0">
                            <p className="text-sm font-semibold text-slate-900 truncate flex items-center gap-1.5">
                              {u.name}
                              {!u._active && <span className="badge badge-neutral">Inactive</span>}
                            </p>
                            <p className="text-[11px] text-slate-400 truncate">{u.job_title || u.department || meta.label}</p>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3.5">
                        <div className="flex items-center gap-1 min-w-0">
                          <Mail size={11} className="text-slate-300 shrink-0" />
                          <span className="text-xs text-slate-600 truncate max-w-[190px]">{u.email}</span>
                          <CopyButton value={u.email} title="Copy email"
                            className="p-1 rounded-md text-slate-300 hover:text-blue-600 hover:bg-blue-50" />
                        </div>
                        {u.phone && (
                          <div className="flex items-center gap-1.5 mt-0.5">
                            <Phone size={10} className="text-slate-300 shrink-0" />
                            <span className="text-[11px] text-slate-400">{u.phone}</span>
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-3.5">
                        <span className={`text-[10px] px-2 py-0.5 rounded-md font-bold inline-flex items-center gap-1 ${meta.badge || "bg-slate-200 text-slate-700"}`}>
                          <Shield size={9} /> {meta.label || u.role}
                        </span>
                      </td>
                      <td className="px-4 py-3.5">
                        {u._orgLabel ? (
                          <span className="flex items-center gap-1.5 text-xs text-slate-600">
                            <MapPin size={11} className="text-amber-500 shrink-0" /> {u._orgLabel}
                          </span>
                        ) : (
                          <span className="flex items-center gap-1.5 text-xs text-slate-400">
                            <Globe size={11} className="text-slate-300 shrink-0" /> All organizations
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3.5">
                        <div className="flex items-center gap-1.5">
                          {isContributor ? <FileText size={11} className="text-violet-500 shrink-0" /> : <ClipboardCheck size={11} className="text-blue-500 shrink-0" />}
                          <span className="text-xs font-bold text-slate-700 tabular-nums">{wl.value}</span>
                          <span className="text-[11px] text-slate-400">{wl.label}</span>
                        </div>
                        {wl.pending > 0 && (
                          <p className="text-[10px] text-amber-600 font-semibold mt-0.5 ml-4">{wl.pending} {isContributor ? "awaiting" : "open"}</p>
                        )}
                      </td>
                      <td className="px-5 py-3.5">
                        <div className="flex items-center justify-end gap-0.5">
                          <a href={mailtoUrl(u.email, "Cyber Assessment Agent — follow-up")} onClick={(e) => e.stopPropagation()} title={`Email ${u.name}`}
                            className="p-1.5 rounded-lg text-slate-300 hover:text-blue-600 hover:bg-blue-50 transition-colors">
                            <Mail size={14} />
                          </a>
                          <button onClick={(e) => { e.stopPropagation(); openEdit(u); }} title="Edit profile"
                            className="p-1.5 rounded-lg text-slate-300 hover:text-blue-600 hover:bg-blue-50 transition-colors">
                            <Pencil size={14} />
                          </button>
                          <button onClick={(e) => { e.stopPropagation(); askToggleActive(u); }} title={u._active ? "Deactivate" : "Reactivate"}
                            className="p-1.5 rounded-lg text-slate-300 hover:text-amber-600 hover:bg-amber-50 transition-colors">
                            <Power size={14} />
                          </button>
                          <button onClick={(e) => { e.stopPropagation(); askDelete(u); }} title="Delete"
                            className="p-1.5 rounded-lg text-slate-300 hover:text-rose-600 hover:bg-rose-50 transition-colors">
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Overlay order matters — all use z-50, so later siblings paint on top. */}
      <UserDetailDrawer user={detailUser} organizations={organizations}
        workload={detailUser ? workload[(detailUser.email || "").toLowerCase()] : null}
        onClose={() => setDetail(null)}
        onEdit={(u) => { setDetail(null); openEdit(u); }}
        onToggleActive={askToggleActive}
        onDelete={askDelete} />

      <UserFormModal open={formOpen} user={editing} organizations={organizations}
        onClose={() => { setFormOpen(false); setEditing(null); }} onSubmit={handleSubmit} />

      <ConfirmDialog open={Boolean(confirm)} busy={confirmBusy}
        title={confirm?.title} message={confirm?.message}
        confirmLabel={confirm?.confirmLabel} tone={confirm?.tone}
        onConfirm={runConfirm} onClose={() => setConfirm(null)} />

      <Toast message={toast} onDone={() => setToast(null)} />
    </div>
  );
}
