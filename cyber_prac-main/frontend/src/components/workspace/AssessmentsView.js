"use client";

import { useEffect, useState, useCallback } from "react";
import { Plus, Search, X, ChevronRight, ClipboardCheck, Trash2, UserPlus, Upload } from "lucide-react";
import { api } from "@/lib/api";
import { canCreate, canDelete, canDo, getIdentity, scopesToAssigned, REVIEWER_ROLES, CONTRIBUTOR_ROLES } from "@/lib/auth";
import { getUsersByRole, getUsersByRoles, getOrganizationList } from "@/lib/users";
import { CountUp } from "@/components/ui/Charts";
import AssessmentWorkspace from "./AssessmentWorkspace";

function AssignModal({ assessment, onClose, onAssign }) {
  const [value, setValue] = useState(assessment.assigned_to || "");
  const [saving, setSaving] = useState(false);
  const reviewers = getUsersByRoles(REVIEWER_ROLES);

  const handleSave = async () => {
    setSaving(true);
    await onAssign(value || null);
    setSaving(false);
  };

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-2xl p-5 w-80 space-y-4" style={{ boxShadow: "var(--shadow-elevated)" }}
        onClick={e => e.stopPropagation()}>
        <div>
          <h3 className="text-sm font-bold text-slate-900">Assign Reviewer</h3>
          <p className="text-xs text-slate-400 mt-0.5 truncate">{assessment.name}</p>
        </div>
        {reviewers.length > 0 ? (
          <select value={value} onChange={e => setValue(e.target.value)}
            className="w-full text-sm border border-slate-200 rounded-xl px-4 py-2.5 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-50 text-slate-900 bg-white">
            <option value="">Unassigned</option>
            {reviewers.map(u => <option key={u.email} value={u.email}>{u.name} · {u.email}</option>)}
          </select>
        ) : (
          <div>
            <input type="email" value={value} onChange={e => setValue(e.target.value)}
              placeholder="reviewer@company.com"
              className="w-full text-sm border border-slate-200 rounded-xl px-4 py-2.5 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-50 text-slate-900" />
            <p className="text-[10px] text-slate-400 mt-1">No reviewers created yet — add them in Users, or enter an email manually.</p>
          </div>
        )}
        <div className="flex gap-2">
          <button onClick={onClose} className="btn-secondary flex-1 justify-center">Cancel</button>
          <button onClick={handleSave} disabled={saving} className="btn-primary flex-1 justify-center">
            {saving ? "Saving…" : "Assign"}
          </button>
        </div>
      </div>
    </div>
  );
}

const STATUS_CONFIG = {
  draft:       { label: "Draft",       badge: "badge-neutral",  dot: "status-draft"       },
  in_progress: { label: "In Progress", badge: "badge-medium",   dot: "status-in_progress" },
  completed:   { label: "Completed",   badge: "badge-success",  dot: "status-completed"   },
};
const INPUT_CLS = "w-full text-sm border border-slate-200 rounded-xl px-4 py-2.5 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-50 transition-all text-slate-900 placeholder:text-slate-400 bg-white";
const STATUS_FILTERS = ["all", "draft", "in_progress", "completed"];
const FILTER_LABELS  = { all: "All", draft: "Draft", in_progress: "In Progress", completed: "Completed" };

function AssessmentCard({ a, onClick }) {
  const cfg = STATUS_CONFIG[a.status] || STATUS_CONFIG.draft;

  return (
    <div onClick={() => onClick(a)}
      className="bg-white border border-slate-100 rounded-2xl p-5 cursor-pointer hover:border-blue-200 hover:shadow-md transition-all"
      style={{ boxShadow: "var(--shadow-card)" }}>
      <div className="flex items-start justify-between mb-3">
        <span className={`badge ${cfg.badge}`}><span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />{cfg.label}</span>
        {(a.status === "in_review" || a.status === "completed") && a.overall_score != null && <span className="text-xl font-bold text-slate-900 tabular-nums">{a.overall_score}%</span>}
      </div>
      <h3 className="text-sm font-bold text-slate-900 mb-0.5 leading-snug">{a.name}</h3>
      {a.market_label && (
        <p className="text-xs font-semibold text-blue-600 mb-0.5">{a.market_label}</p>
      )}
      {a.organization && <p className="text-xs text-slate-400">{a.organization}</p>}
      <button className="mt-4 w-full btn-primary text-xs justify-center">
        {a.status === "completed" ? "Review Results" : a.status === "in_progress" ? "Continue →" : "Start →"}
      </button>
    </div>
  );
}

const SCOPE_LEVELS = [
  { key: "global",  label: "Global"    },
  { key: "organization", label: "Organization" },
];

// Per-organization personnel row (reviewers + contributors)
function OrganizationPersonnelRow({ organization, reviewerList, contributorList, onChange }) {
  const [reviewerInput, setReviewerInput] = useState("");
  const [contributorInput,    setContributorInput]    = useState("");

  const addReviewer = (email) => {
    if (!email) return;
    onChange(organization.id, "reviewers", [...(organization.reviewers || []), email.toLowerCase().trim()]);
    setReviewerInput("");
  };
  const removeReviewer = (email) =>
    onChange(organization.id, "reviewers", (organization.reviewers || []).filter(e => e !== email));

  const addContributor = (email) => {
    if (!email) return;
    onChange(organization.id, "contributors", [...(organization.contributors || []), email.toLowerCase().trim()]);
    setContributorInput("");
  };
  const removeContributor = (email) =>
    onChange(organization.id, "contributors", (organization.contributors || []).filter(e => e !== email));

  return (
    <div className="border border-slate-200 rounded-xl p-3 space-y-3 bg-slate-50/60">
      <p className="text-xs font-bold text-slate-700">{organization.label}</p>

      {/* Reviewers */}
      <div>
        <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Reviewers</p>
        <div className="flex gap-1.5 mb-1.5">
          {reviewerList.length > 0 ? (
            <select value="" onChange={e => addReviewer(e.target.value)}
              className="flex-1 text-xs border border-slate-200 rounded-lg px-2 py-1.5 bg-white text-slate-700 outline-none focus:border-blue-400">
              <option value="">Add reviewer…</option>
              {reviewerList.filter(u => !(organization.reviewers || []).includes(u.email))
                .map(u => <option key={u.email} value={u.email}>{u.name} · {u.email}</option>)}
            </select>
          ) : (
            <input type="email" value={reviewerInput}
              onChange={e => setReviewerInput(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); addReviewer(reviewerInput); } }}
              placeholder="reviewer@company.com"
              className="flex-1 text-xs border border-slate-200 rounded-lg px-2 py-1.5 bg-white outline-none focus:border-blue-400" />
          )}
          {reviewerList.length === 0 && (
            <button type="button" onClick={() => addReviewer(reviewerInput)}
              className="text-xs px-2 py-1.5 rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-blue-50 hover:border-blue-300">+</button>
          )}
        </div>
        <div className="flex flex-wrap gap-1">
          {(organization.reviewers || []).map(email => (
            <span key={email} className="inline-flex items-center gap-1 text-[11px] bg-blue-50 text-blue-700 rounded-full px-2 py-0.5">
              {email}
              <button type="button" onClick={() => removeReviewer(email)} className="text-blue-400 hover:text-blue-700"><X size={10} /></button>
            </span>
          ))}
        </div>
      </div>

      {/* Contributors */}
      <div>
        <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Contributors (Evidence Providers)</p>
        <div className="flex gap-1.5 mb-1.5">
          {contributorList.length > 0 ? (
            <select value="" onChange={e => addContributor(e.target.value)}
              className="flex-1 text-xs border border-slate-200 rounded-lg px-2 py-1.5 bg-white text-slate-700 outline-none focus:border-blue-400">
              <option value="">Add contributor…</option>
              {contributorList.filter(u => !(organization.contributors || []).includes(u.email))
                .map(u => <option key={u.email} value={u.email}>{u.name} · {u.email}</option>)}
            </select>
          ) : (
            <input type="email" value={contributorInput}
              onChange={e => setContributorInput(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); addContributor(contributorInput); } }}
              placeholder="contributor@company.com"
              className="flex-1 text-xs border border-slate-200 rounded-lg px-2 py-1.5 bg-white outline-none focus:border-blue-400" />
          )}
          {contributorList.length === 0 && (
            <button type="button" onClick={() => addContributor(contributorInput)}
              className="text-xs px-2 py-1.5 rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-blue-50 hover:border-blue-300">+</button>
          )}
        </div>
        <div className="flex flex-wrap gap-1">
          {(organization.contributors || []).map(email => (
            <span key={email} className="inline-flex items-center gap-1 text-[11px] bg-emerald-50 text-emerald-700 rounded-full px-2 py-0.5">
              {email}
              <button type="button" onClick={() => removeContributor(email)} className="text-emerald-400 hover:text-emerald-700"><X size={10} /></button>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

function BulkAssignRow({ reviewerList, contributorList, onBulkAssign }) {
  const [reviewerInput, setReviewerInput] = useState("");
  const [contributorInput,    setContributorInput]    = useState("");
  return (
    <div className="bg-blue-50/60 border border-blue-100 rounded-xl p-3 space-y-2">
      <p className="text-[10px] font-bold text-blue-700 uppercase tracking-wider">Quick-Assign to All Organizations</p>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <p className="text-[10px] text-slate-500 mb-1">Reviewer</p>
          {reviewerList.length > 0 ? (
            <select value="" onChange={e => { if (e.target.value) onBulkAssign("reviewers", e.target.value); }}
              className="w-full text-xs border border-slate-200 rounded-lg px-2 py-1.5 bg-white text-slate-700 outline-none focus:border-blue-400">
              <option value="">Add to all…</option>
              {reviewerList.map(u => <option key={u.email} value={u.email}>{u.name}</option>)}
            </select>
          ) : (
            <div className="flex gap-1">
              <input type="email" value={reviewerInput} onChange={e => setReviewerInput(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); onBulkAssign("reviewers", reviewerInput); setReviewerInput(""); }}}
                placeholder="reviewer@…"
                className="flex-1 text-xs border border-slate-200 rounded-lg px-2 py-1.5 outline-none focus:border-blue-400" />
              <button type="button" onClick={() => { onBulkAssign("reviewers", reviewerInput); setReviewerInput(""); }}
                className="text-xs px-2 py-1.5 border border-slate-200 rounded-lg bg-white hover:bg-blue-50">+</button>
            </div>
          )}
        </div>
        <div>
          <p className="text-[10px] text-slate-500 mb-1">Contributor (Evidence)</p>
          {contributorList.length > 0 ? (
            <select value="" onChange={e => { if (e.target.value) onBulkAssign("contributors", e.target.value); }}
              className="w-full text-xs border border-slate-200 rounded-lg px-2 py-1.5 bg-white text-slate-700 outline-none focus:border-blue-400">
              <option value="">Add to all…</option>
              {contributorList.map(u => <option key={u.email} value={u.email}>{u.name}</option>)}
            </select>
          ) : (
            <div className="flex gap-1">
              <input type="email" value={contributorInput} onChange={e => setContributorInput(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); onBulkAssign("contributors", contributorInput); setContributorInput(""); }}}
                placeholder="contributor@…"
                className="flex-1 text-xs border border-slate-200 rounded-lg px-2 py-1.5 outline-none focus:border-blue-400" />
              <button type="button" onClick={() => { onBulkAssign("contributors", contributorInput); setContributorInput(""); }}
                className="text-xs px-2 py-1.5 border border-slate-200 rounded-lg bg-white hover:bg-blue-50">+</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// How the AI generates the questionnaire. Mirrors the backend
// GenerationConfig.strategy enum. A choice is required once a framework is
// selected (the designer decides per-assessment — no silent default).
const GEN_STRATEGIES = [
  { key: "framework_only",    label: "Framework questions only",
    desc: "Use the framework's imported questions as-is. Fast, deterministic, no AI." },
  { key: "framework_plus_ai", label: "Framework + AI diagnostic",
    desc: "Keep framework questions and add maturity-graded diagnostic questions. Most thorough." },
  { key: "ai_rewrite",        label: "AI-rewrite framework questions",
    desc: "Replace framework questions with AI diagnostic questions grounded in the maturity rubric." },
];

function CreateForm({ onCreated, onCancel, canAssign }) {
  const [frameworks,     setFrameworks]     = useState([]);
  const [domains,        setDomains]        = useState([]);
  const [selDomains,     setSelDomains]     = useState([]);
  const [form,           setForm]           = useState({ name: "", framework_id: "", description: "" });
  const [scopeLevel,     setScopeLevel]     = useState("organization");
  // selOrgs: array of { id, label, path }
  const [selOrgs,     setSelOrgs]     = useState([]);
  const [preFile,        setPreFile]        = useState(null);
  const [creating,       setCreating]       = useState(false);
  const [genCfg,         setGenCfg]         = useState({
    strategy: "", depth: "standard", evidence_policy: "high_and_critical",
    recommendation_mode: "hybrid", custom_instructions: "",
  });
  const setGen = (k) => (e) => setGenCfg((c) => ({ ...c, [k]: e.target.value }));

  const reviewerList = canAssign ? getUsersByRoles(REVIEWER_ROLES) : [];
  const contributorList    = getUsersByRoles(CONTRIBUTOR_ROLES);

  const organizations = getOrganizationList();

  useEffect(() => { api.frameworks.list().then(setFrameworks).catch(console.error); }, []);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const handleFrameworkChange = async (e) => {
    const id = e.target.value;
    setForm((f) => ({ ...f, framework_id: id }));
    setDomains([]); setSelDomains([]);
    if (!id) return;
    try {
      const fw = await api.frameworks.get(id);
      const ds = fw.domains || [];
      setDomains(ds);
      setSelDomains(ds.map((d) => `${id}:${d.code}`));
    } catch (err) { console.error(err); }
  };

  const toggleDomain = (key) =>
    setSelDomains((s) => s.includes(key) ? s.filter((k) => k !== key) : [...s, key]);

  const selectOrganization = (m) =>
    setSelOrgs([{ id: m.id, label: m.label, path: m.path }]);

  const buildOrganization = () => {
    if (scopeLevel === "global") return "";
    if (scopeLevel === "organization") return selOrgs[0]?.label || "";
    return "";
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!form.name.trim()) return;
    if (form.framework_id && !genCfg.strategy) {
      alert("Please choose how the AI should generate the questionnaire (AI Generation)."); return;
    }
    if (scopeLevel === "organization" && selOrgs.length === 0) {
      alert("Please select an organization."); return;
    }
    setCreating(true);
    const org = buildOrganization();

    try {
      const a = await api.assessments.create({
        name: form.name.trim(),
        description: form.description || undefined,
        framework_ids: form.framework_id ? [form.framework_id] : [],
        selected_domains: selDomains,
        organization: org || undefined,
        market_id: selOrgs[0]?.id || undefined,
        market_label: selOrgs[0]?.label || undefined,
        generation_config: form.framework_id ? {
          strategy: genCfg.strategy,
          depth: genCfg.depth,
          evidence_policy: genCfg.evidence_policy,
          recommendation_mode: genCfg.recommendation_mode,
          custom_instructions: genCfg.custom_instructions || undefined,
        } : undefined,
      });
      if (preFile) {
        try { await api.assessments.uploadPreAssessment(a.id, preFile); }
        catch (err) { alert(`Assessment created, but the pre-assessment file could not be parsed: ${err.message}`); }
      }
      onCreated(a);
    } catch (err) { alert(err.message); } finally { setCreating(false); }
  };

  return (
    <div className="max-w-lg mx-auto py-8 px-6 fade-in-up">
      <div className="flex items-center gap-3 mb-6">
        <button onClick={onCancel} className="p-2 rounded-lg hover:bg-slate-100 transition-colors">
          <X size={15} className="text-slate-500" />
        </button>
        <div>
          <h2 className="text-base font-bold text-slate-900">New Vendor Assessment</h2>
          <p className="text-xs text-slate-400">Configure a third party vendor review</p>
        </div>
      </div>
      <form onSubmit={handleSubmit} className="bg-white border border-slate-200 rounded-2xl p-6 space-y-4" style={{ boxShadow: "var(--shadow-elevated)" }}>
        <div>
          <label className="block text-xs font-bold text-slate-600 mb-2 uppercase tracking-wider">Assessment Name *</label>
          <input value={form.name} onChange={set("name")} placeholder="e.g. Q4 NIST CSF Audit 2026" required className={INPUT_CLS} />
        </div>
        <div>
          <label className="block text-xs font-bold text-slate-600 mb-2 uppercase tracking-wider">Framework</label>
          <select value={form.framework_id} onChange={handleFrameworkChange} className={INPUT_CLS}>
            <option value="">Select framework (optional)</option>
            {frameworks.map(fw => <option key={fw.id} value={fw.id}>{fw.name} v{fw.version}</option>)}
          </select>
        </div>
        {domains.length > 0 && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-xs font-bold text-slate-600 uppercase tracking-wider">
                Domains <span className="text-slate-400">({selDomains.length}/{domains.length})</span>
              </label>
              <button type="button" className="text-[11px] font-semibold text-blue-600 hover:underline"
                onClick={() => setSelDomains(selDomains.length === domains.length ? [] : domains.map(d => `${form.framework_id}:${d.code}`))}>
                {selDomains.length === domains.length ? "Clear all" : "Select all"}
              </button>
            </div>
            <div className="max-h-44 overflow-y-auto border border-slate-200 rounded-xl divide-y divide-slate-100">
              {domains.map(d => {
                const key = `${form.framework_id}:${d.code}`;
                return (
                  <label key={d.id} className="flex items-center gap-2.5 px-3 py-2 hover:bg-slate-50 cursor-pointer">
                    <input type="checkbox" checked={selDomains.includes(key)} onChange={() => toggleDomain(key)} className="accent-blue-600" />
                    <span className="text-xs font-mono font-semibold text-slate-500 shrink-0">{d.code}</span>
                    <span className="text-sm text-slate-700 truncate">{d.name}</span>
                    <span className="text-[10px] text-slate-400 ml-auto shrink-0">{(d.controls || []).length || ""}</span>
                  </label>
                );
              })}
            </div>
            <p className="text-[10px] text-slate-400 mt-1">Only the selected domains are included in the questionnaire.</p>
          </div>
        )}
        {/* AI Generation Settings — how the questionnaire is produced */}
        {form.framework_id && (
          <div className="border border-slate-200 rounded-xl p-4 space-y-3.5 bg-slate-50/40">
            <div>
              <label className="block text-xs font-bold text-slate-600 mb-1 uppercase tracking-wider">
                AI Generation <span className="text-rose-500">*</span>
              </label>
              <p className="text-[10px] text-slate-400 mb-2">Choose how this assessment&apos;s questionnaire is produced.</p>
              <div className="space-y-1.5">
                {GEN_STRATEGIES.map((s) => (
                  <label key={s.key}
                    className={`flex items-start gap-2.5 px-3 py-2 rounded-lg border cursor-pointer transition-colors ${
                      genCfg.strategy === s.key ? "border-blue-600 bg-blue-50" : "border-slate-200 hover:bg-slate-50"}`}>
                    <input type="radio" name="gen-strategy" className="accent-blue-600 mt-0.5"
                      checked={genCfg.strategy === s.key}
                      onChange={() => setGenCfg((c) => ({ ...c, strategy: s.key }))} />
                    <span className="min-w-0">
                      <span className="block text-xs font-semibold text-slate-800">{s.label}</span>
                      <span className="block text-[10px] text-slate-500 leading-snug">{s.desc}</span>
                    </span>
                  </label>
                ))}
              </div>
            </div>

            {genCfg.strategy && genCfg.strategy !== "framework_only" && (
              <>
                <div>
                  <label className="block text-[11px] font-bold text-slate-600 mb-1.5 uppercase tracking-wider">Depth</label>
                  <div className="grid grid-cols-2 gap-2">
                    {[["standard", "Standard", "~6 / domain"], ["detailed", "Detailed", "~10 / domain"]].map(([k, l, h]) => (
                      <button key={k} type="button" onClick={() => setGenCfg((c) => ({ ...c, depth: k }))}
                        className={`text-xs font-semibold py-2 rounded-lg border transition-colors ${
                          genCfg.depth === k ? "border-blue-600 bg-blue-50 text-blue-700" : "border-slate-200 text-slate-500 hover:bg-slate-50"}`}>
                        {l}<span className="block text-[9px] font-normal text-slate-400">{h}</span>
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="block text-[11px] font-bold text-slate-600 mb-1.5 uppercase tracking-wider">Custom Instructions</label>
                  <textarea value={genCfg.custom_instructions} onChange={setGen("custom_instructions")} rows={2}
                    placeholder="Optional — guide the AI: focus areas, tone, question limits…"
                    className={`${INPUT_CLS} resize-none`} />
                </div>
              </>
            )}

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] font-bold text-slate-600 mb-1.5 uppercase tracking-wider">Evidence</label>
                <select value={genCfg.evidence_policy} onChange={setGen("evidence_policy")} className={INPUT_CLS}>
                  <option value="always">Always request</option>
                  <option value="high_and_critical">High-risk & critical gaps</option>
                  <option value="optional">Optional</option>
                </select>
              </div>
              <div>
                <label className="block text-[11px] font-bold text-slate-600 mb-1.5 uppercase tracking-wider">Recommendations</label>
                <select value={genCfg.recommendation_mode} onChange={setGen("recommendation_mode")} className={INPUT_CLS}>
                  <option value="framework">Framework</option>
                  <option value="ai">AI-generated</option>
                  <option value="hybrid">Hybrid</option>
                </select>
              </div>
            </div>
          </div>
        )}
        {/* Scope selector */}
        <div>
          <label className="block text-xs font-bold text-slate-600 mb-2 uppercase tracking-wider">Scope</label>
          <div className="grid grid-cols-4 gap-2 mb-3">
            {SCOPE_LEVELS.map(({ key, label }) => (
              <button key={key} type="button"
                onClick={() => { setScopeLevel(key); setSelOrgs([]); }}
                className={`text-xs font-semibold py-2 rounded-lg border transition-colors ${
                  scopeLevel === key
                    ? "border-blue-600 bg-blue-50 text-blue-700"
                    : "border-slate-200 text-slate-500 hover:border-slate-300 hover:bg-slate-50"
                }`}>
                {label}
              </button>
            ))}
          </div>

          {scopeLevel === "global" && (
            <p className="text-xs text-slate-400 bg-slate-50 rounded-xl px-4 py-3">
              This assessment applies to <strong className="text-slate-600">all organizations</strong>.
            </p>
          )}

          {scopeLevel === "organization" && (
            <>
              {organizations.length > 0 ? (
                <div className="max-h-36 overflow-y-auto border border-slate-200 rounded-xl divide-y divide-slate-100">
                  {organizations.map(m => (
                    <label key={m.id} className="flex items-center gap-2.5 px-3 py-2 hover:bg-slate-50 cursor-pointer">
                      <input type="radio" name="organization-select" className="accent-blue-600"
                        checked={selOrgs[0]?.id === m.id}
                        onChange={() => selectOrganization(m)} />
                      <span className="text-xs text-slate-600 truncate">{m.path}</span>
                    </label>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-slate-400 bg-slate-50 rounded-xl px-4 py-3">
                  No organizations yet — assign an organization to a person in <strong className="text-slate-600">Users</strong> first.
                </p>
              )}
              {selOrgs.length === 0 && (
                <p className="text-[10px] text-slate-400">Select an organization.</p>
              )}
              {selOrgs.length > 0 && (
                <p className="text-[10px] text-blue-600 font-medium">{selOrgs[0].label} selected</p>
              )}
            </>
          )}
        </div>
        <div>
          <label className="block text-xs font-bold text-slate-600 mb-2 uppercase tracking-wider">Description</label>
          <textarea value={form.description} onChange={set("description")} placeholder="Optional scope or notes…" rows={2} className={`${INPUT_CLS} resize-none`} />
        </div>
        <div>
          <label className="block text-xs font-bold text-slate-600 mb-2 uppercase tracking-wider">Custom Pre-Assessment Questionnaire</label>
          <label className="flex items-center gap-2.5 border-2 border-dashed border-slate-200 rounded-xl px-4 py-3 cursor-pointer hover:border-blue-400 transition-colors">
            <Upload size={15} className="text-blue-500 shrink-0" />
            <span className="text-xs text-slate-600 truncate flex-1">
              {preFile ? preFile.name : "Upload a custom questionnaire (JSON, CSV, TXT, DOCX, PDF)"}
            </span>
            {preFile && (
              <button type="button" onClick={(e) => { e.preventDefault(); setPreFile(null); }}
                className="text-slate-400 hover:text-rose-600"><X size={13} /></button>
            )}
            <input type="file" className="hidden" accept=".json,.csv,.txt,.md,.docx,.doc,.pdf,.xlsx,.xls"
              onChange={(e) => setPreFile(e.target.files?.[0] || null)} />
          </label>
          <p className="text-[10px] text-slate-400 mt-1">Optional — if left empty, the standard default questionnaire is used automatically.</p>
        </div>
        <div className="flex gap-3 pt-2">
          <button type="button" onClick={onCancel} className="btn-secondary flex-1 justify-center">Cancel</button>
          <button type="submit" disabled={creating || !form.name.trim() || (!!form.framework_id && !genCfg.strategy)} className="btn-primary flex-1 justify-center">
            {creating ? "Creating…" : "Create Assessment"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function AssessmentsView({ setActiveView, role }) {
  const [mode,        setMode]        = useState("list");
  const [assessments, setAssessments] = useState([]);
  const [selected,    setSelected]    = useState(null);
  const [loading,     setLoading]     = useState(true);
  const [search,      setSearch]      = useState("");
  const [statusFilter,setStatusFilter]= useState("all");
  const [deletingId,  setDeletingId]  = useState(null);
  const [assigningId, setAssigningId] = useState(null);
  const [assignModal, setAssignModal] = useState(null);  // assessment object when modal open
  const isCardView   = role === "auditor";
  const allowDelete  = canDelete(role, "assessment");
  const allowAssign  = canDo(role, "assignAssessment");
  const identity     = getIdentity();

  const load = useCallback(() => {
    setLoading(true);
    api.assessments.list()
      .then((list) => {
        // An Auditor only sees the assessments assigned to their identity.
        const id = (identity || "").toLowerCase();
        const scoped = scopesToAssigned(role)
          ? list.filter((a) => {
              if (a.assigned_to && a.assigned_to.toLowerCase() === id) return true;
              return false;
            })
          : list;
        setAssessments(scoped.filter((a) => !["[framework-evidence-assessment]", "[compliance-readiness]"].some(marker => (a.description || "").includes(marker))));
      })
      .catch(console.error).finally(() => setLoading(false));
  }, [role, identity]);
  useEffect(() => {
    Promise.resolve().then(load);
  }, [load]);

  const handleAssign = (e, a) => { e.stopPropagation(); setAssignModal(a); };

  const handleAssignSave = async (email) => {
    if (!assignModal) return;
    setAssigningId(assignModal.id);
    try {
      const res = await api.assessments.assign(assignModal.id, email);
      setAssessments((list) => list.map((x) => x.id === assignModal.id ? { ...x, assigned_to: res.assigned_to } : x));
    } catch (err) { alert(err.message); }
    finally { setAssigningId(null); setAssignModal(null); }
  };

  const handleCreated = (a) => { load(); setSelected(a); setMode("workspace"); };
  const handleOpen    = (a) => { setSelected(a); setMode("workspace"); };
  const handleDelete  = async (e, a) => {
    e.stopPropagation();
    if (!window.confirm(`Delete assessment "${a.name}"? This permanently removes its questionnaire, responses, evidence, findings and scores.`)) return;
    setDeletingId(a.id);
    try {
      await api.assessments.delete(a.id);
      setAssessments((list) => list.filter((x) => x.id !== a.id));
    } catch (err) {
      alert(err.message);
    } finally {
      setDeletingId(null);
    }
  };

  const counts = {
    all:         assessments.length,
    draft:       assessments.filter(a => a.status === "draft").length,
    in_progress: assessments.filter(a => a.status === "in_progress").length,
    completed:   assessments.filter(a => a.status === "completed").length,
  };

  const filtered = assessments.filter(a => {
    const matchSearch = !search || a.name.toLowerCase().includes(search.toLowerCase()) || a.organization?.toLowerCase().includes(search.toLowerCase());
    const matchStatus = statusFilter === "all" || a.status === statusFilter;
    return matchSearch && matchStatus;
  });

  if (mode === "workspace" && selected) {
    return (
      <div style={{ height: "calc(100vh - 3.5rem)" }}>
        <AssessmentWorkspace assessment={selected} role={role} onBack={() => { setMode("list"); load(); }} onComplete={load} />
      </div>
    );
  }
  if (mode === "create") return <CreateForm onCreated={handleCreated} onCancel={() => setMode("list")} canAssign={allowAssign} />;

  return (
    <>
    <div className="p-6 max-w-6xl mx-auto space-y-5 fade-in-up">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Third Party Vendor Management</h2>
          <p className="text-sm text-slate-500 mt-1">{assessments.length} total · {counts.completed} completed</p>
        </div>
        {canCreate(role, "assessment") && (
          <button onClick={() => setMode("create")} className="btn-primary"><Plus size={14} /> New Vendor Assessment</button>
        )}
      </div>

      {/* Metric cards */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: "Total",       value: counts.all,         accent: "text-slate-900" },
          { label: "Draft",       value: counts.draft,       accent: "text-slate-500" },
          { label: "In Progress", value: counts.in_progress, accent: "text-amber-600" },
          { label: "Completed",   value: counts.completed,   accent: "text-emerald-600" },
        ].map(({ label, value, accent }) => (
          <div key={label} className="bg-white border border-slate-100 rounded-2xl p-4" style={{ boxShadow: "var(--shadow-card)" }}>
            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">{label}</p>
            <p className={`text-2xl font-bold tabular-nums ${accent}`}>
              {loading ? <span className="skeleton inline-block w-8 h-7 rounded" /> : <CountUp to={value} />}
            </p>
          </div>
        ))}
      </div>

      {/* Search + filter row */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2 bg-white border border-slate-200 rounded-xl px-4 py-2.5 flex-1 max-w-sm focus-within:border-blue-400 focus-within:ring-2 focus-within:ring-blue-50 transition-all">
          <Search size={14} className="text-slate-400 shrink-0" />
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search by name or organization…"
            className="flex-1 text-sm text-slate-700 outline-none placeholder:text-slate-400 bg-transparent" />
          {search && <button onClick={() => setSearch("")} className="text-slate-400 hover:text-slate-600"><X size={12} /></button>}
        </div>
        <div className="flex gap-1 bg-white border border-slate-200 rounded-xl p-1">
          {STATUS_FILTERS.map(f => (
            <button key={f} onClick={() => setStatusFilter(f)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors whitespace-nowrap ${
                statusFilter === f ? "bg-blue-700 text-white shadow-sm" : "text-slate-500 hover:bg-slate-50"}`}>
              {FILTER_LABELS[f]}
            </button>
          ))}
        </div>
      </div>

      {/* Card view (assessor — assigned assessments) */}
      {isCardView ? (
        loading ? (
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
            {[1,2,3,4].map(i => <div key={i} className="skeleton h-40 rounded-2xl" />)}
          </div>
        ) : filtered.length === 0 ? (
          <div className="bg-white border border-slate-100 rounded-2xl py-16 text-center" style={{ boxShadow: "var(--shadow-card)" }}>
            <ClipboardCheck size={28} className="text-slate-200 mx-auto mb-3" />
            <p className="text-sm font-medium text-slate-500">{search ? `No results for "${search}"` : "No assessments assigned."}</p>
          </div>
        ) : (
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
            {filtered.map(a => <AssessmentCard key={a.id} a={a} onClick={handleOpen} />)}
          </div>
        )
      ) : (
        /* Table view (org owner / managers) */
        <div className="bg-white border border-slate-100 rounded-2xl overflow-hidden" style={{ boxShadow: "var(--shadow-card)" }}>
          {loading ? (
            <div className="p-6 space-y-3">{[1,2,3,4].map(i => <div key={i} className="skeleton h-14 rounded-xl" />)}</div>
          ) : filtered.length === 0 ? (
            <div className="py-16 text-center">
              <ClipboardCheck size={28} className="text-slate-200 mx-auto mb-3" />
              <p className="text-sm font-medium text-slate-500">{search ? `No results for "${search}"` : "No assessments yet"}</p>
              {!search && canCreate(role, "assessment") && (
                <button onClick={() => setMode("create")} className="text-xs text-blue-600 hover:underline mt-2">Create your first assessment →</button>
              )}
            </div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50/60">
                  {["Name", "Organization", "Assigned To", "Status", "Score", "Created", ""].map(h => (
                    <th key={h} className="text-left px-5 py-3 text-[11px] font-bold text-slate-400 uppercase tracking-wider last:w-8">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map((a, idx) => {
                  const cfg = STATUS_CONFIG[a.status] || STATUS_CONFIG.draft;
                  return (
                    <tr key={a.id} onClick={() => handleOpen(a)}
                      className="border-b border-slate-50 last:border-0 hover:bg-blue-50/30 cursor-pointer transition-colors group"
                      style={{ animationDelay: `${idx * 0.03}s` }}>
                      <td className="px-5 py-4">
                        <p className="text-sm font-semibold text-slate-900 group-hover:text-blue-700 transition-colors">{a.name}</p>
                      </td>
                      <td className="px-5 py-4 text-xs text-slate-400">
                        {a.market_label
                          ? <span className="text-blue-700 font-semibold">{a.market_label}</span>
                          : (a.organization || "—")}
                      </td>
                      <td className="px-5 py-4 text-xs">
                        {a.assigned_to
                          ? <span className="text-slate-600">{a.assigned_to}</span>
                          : <span className="text-slate-300">—</span>}
                      </td>
                      <td className="px-5 py-4">
                        <span className={`badge ${cfg.badge}`}><span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />{cfg.label}</span>
                      </td>
                      <td className="px-5 py-4">
                        <span className="text-sm font-bold text-slate-900 tabular-nums">{(a.status === "in_review" || a.status === "completed") && a.overall_score != null ? `${a.overall_score}%` : "—"}</span>
                      </td>
                      <td className="px-5 py-4 text-xs text-slate-400 tabular-nums">
                        {new Date(a.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
                      </td>
                      <td className="px-5 py-4 text-right">
                        <div className="flex items-center justify-end gap-1">
                          {allowAssign && (
                            <button
                              onClick={(e) => handleAssign(e, a)}
                              disabled={assigningId === a.id}
                              title="Assign to reviewer"
                              className="p-1.5 rounded-lg text-slate-300 hover:text-blue-600 hover:bg-blue-50 transition-colors disabled:opacity-40"
                            >
                              <UserPlus size={15} />
                            </button>
                          )}
                          {allowDelete && (
                            <button
                              onClick={(e) => handleDelete(e, a)}
                              disabled={deletingId === a.id}
                              title="Delete assessment"
                              className="p-1.5 rounded-lg text-slate-300 hover:text-red-600 hover:bg-red-50 transition-colors disabled:opacity-40"
                            >
                              <Trash2 size={15} />
                            </button>
                          )}
                          <ChevronRight size={15} className="text-slate-300 group-hover:text-blue-500 transition-colors" />
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>

    {assignModal && (
      <AssignModal
        assessment={assignModal}
        onClose={() => setAssignModal(null)}
        onAssign={handleAssignSave}
      />
    )}
    </>
  );
}




