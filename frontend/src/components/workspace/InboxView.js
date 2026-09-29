"use client";

import { useEffect, useState, useCallback } from "react";
import { Star, ClipboardCheck, FileText, ChevronRight, Upload, Bell, Check, MapPin, User } from "lucide-react";
import { api } from "@/lib/api";
import { getIdentity, isContributor } from "@/lib/auth";
import AssessmentWorkspace from "./AssessmentWorkspace";

const REQ_STATUS = {
  requested: { badge: "badge-medium",  label: "Action needed"   },
  provided:  { badge: "badge-low",     label: "Sent to reviewer" },
  accepted:  { badge: "badge-success", label: "Accepted"        },
  rejected:  { badge: "badge-high",    label: "Re-upload needed" },
};

// Contributor notification feed: document requests sent by a reviewer, with inline
// upload. Files uploaded here are auto-linked to the request and land in the
// assessment's engagement documents for the reviewer.
function DocumentRequestsInbox({ requests, onProvided }) {
  const [busyId, setBusyId] = useState(null);

  const provide = async (r, fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    setBusyId(r.id);
    try {
      await api.assessments.provideDocuments(r.assessment_id, r.id, files, getIdentity() || null);
      await onProvided();
    } catch (e) { alert(e.message); } finally { setBusyId(null); }
  };

  const open = requests.filter((r) => r.status === "requested" || r.status === "rejected");
  const done = requests.filter((r) => r.status === "provided" || r.status === "accepted");

  const renderRequest = (r) => {
    const st = REQ_STATUS[r.status] || REQ_STATUS.requested;
    const busy = busyId === r.id;
    const actionable = r.status === "requested" || r.status === "rejected";
    return (
      <div key={r.id} className="bg-white border border-slate-100 rounded-xl px-4 py-3 space-y-1.5"
        style={{ boxShadow: "var(--shadow-card)" }}>
        <div className="flex items-center gap-2 flex-wrap">
          {r.domain_code && <span className="badge badge-neutral font-mono text-[10px]">{r.domain_code}</span>}
          <span className="text-sm font-semibold text-slate-900 flex-1 min-w-0">{r.evidence_type}</span>
          <span className={`badge ${st.badge} shrink-0`}>{st.label}</span>
        </div>
        <p className="text-[11px] text-slate-400 flex items-center gap-1 flex-wrap">
          {r.assessment_name}{r.organization ? ` · ${r.organization}` : ""}
          {r.market_label && (
            <span className="inline-flex items-center gap-0.5 text-blue-600 font-medium">
              <MapPin size={9} /> {r.market_label}
            </span>
          )}
        </p>
        {r.note && <p className="text-[11px] text-slate-500">Reviewer note: {r.note}</p>}
        {r.status === "rejected" && r.review_note && (
          <p className="text-[11px] text-rose-600">Rejected: {r.review_note}</p>
        )}
        {(r.provided_files || []).length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {r.provided_files.map((f, i) => (
              <span key={i} className="badge badge-low text-[10px] flex items-center gap-1">
                <FileText size={10} /> {f.original_name}
              </span>
            ))}
          </div>
        )}
        {actionable && (
          <label className="btn-primary text-[11px] px-3 py-1.5 cursor-pointer inline-flex mt-1">
            <Upload size={11} /> {busy ? "Uploading…" : "Upload requested documents"}
            <input type="file" multiple className="hidden" disabled={busy}
              onChange={(e) => { provide(r, e.target.files); e.target.value = ""; }} />
          </label>
        )}
        {r.status === "accepted" && (
          <p className="text-[11px] text-emerald-600 flex items-center gap-1"><Check size={11} /> Accepted by the reviewer.</p>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-2 mb-3">
          <Bell size={14} className="text-slate-400" />
          <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider">Documents Requested From You</h3>
          <span className="badge badge-neutral ml-auto">{open.length}</span>
        </div>
        {open.length === 0
          ? <p className="text-xs text-slate-400 py-3 px-2">No pending document requests. You&apos;re all caught up.</p>
          : <div className="space-y-2">{open.map(renderRequest)}</div>}
      </div>
      {done.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <ClipboardCheck size={14} className="text-slate-400" />
            <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider">Provided</h3>
            <span className="badge badge-neutral ml-auto">{done.length}</span>
          </div>
          <div className="space-y-2">{done.map(renderRequest)}</div>
        </div>
      )}
    </div>
  );
}

const STATUS_CONFIG = {
  draft:       { label: "Draft",       badge: "badge-neutral",  dot: "status-draft"       },
  in_progress: { label: "In Progress", badge: "badge-medium",   dot: "status-in_progress" },
  completed:   { label: "Completed",   badge: "badge-success",  dot: "status-completed"   },
};

function InboxSection({ title, icon: Icon, items, onOpen, emptyMsg }) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <Icon size={14} className="text-slate-400" />
        <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider">{title}</h3>
        <span className="badge badge-neutral ml-auto">{items.length}</span>
      </div>
      {items.length === 0 ? (
        <p className="text-xs text-slate-400 py-3 px-2">{emptyMsg}</p>
      ) : (
        <div className="space-y-2">
          {items.map(a => {
            const cfg = STATUS_CONFIG[a.status] || STATUS_CONFIG.draft;
            return (
              <div key={a.id} onClick={() => onOpen(a)}
                className="bg-white border border-slate-100 rounded-xl px-4 py-3 flex items-center gap-3 hover:border-blue-200 hover:bg-blue-50/30 cursor-pointer transition-all group"
                style={{ boxShadow: "var(--shadow-card)" }}>
                <span className={`w-2 h-2 rounded-full shrink-0 ${cfg.dot}`} />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold text-slate-900 truncate group-hover:text-blue-700 transition-colors">{a.name}</p>
                  {a.organization && <p className="text-xs text-slate-400">{a.organization}</p>}
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  {(a.status === "in_review" || a.status === "completed") && a.overall_score != null && (
                    <span className="text-sm font-bold text-slate-700 tabular-nums">{a.overall_score}%</span>
                  )}
                  <span className={`badge ${cfg.badge}`}>{cfg.label}</span>
                  <ChevronRight size={14} className="text-slate-300 group-hover:text-blue-400 transition-colors" />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function InboxView({ role, setActiveView }) {
  const [assessments, setAssessments] = useState([]);
  const [docRequests, setDocRequests] = useState([]);
  const [selected,    setSelected]    = useState(null);
  const [loading,     setLoading]     = useState(true);
  const isContributorView = isContributor(role);

  const load = useCallback(() => {
    setLoading(true);
    const jobs = [api.assessments.list().then(setAssessments)];
    jobs.push(api.documentRequests.listAll().then(setDocRequests).catch(console.error));
    Promise.all(jobs).catch(console.error).finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);

  if (selected) {
    return (
      <div style={{ height: "calc(100vh - 3.5rem)" }}>
        <AssessmentWorkspace
          assessment={selected}
          role={role}
          onBack={() => { setSelected(null); load(); }}
          onComplete={load}
        />
      </div>
    );
  }

  const needsScoring = assessments.filter(a => a.status === "in_progress" && a.overall_score == null);
  const completed    = assessments.filter(a => a.status === "completed");
  const drafts       = assessments.filter(a => a.status === "draft");
  const providedDocs = docRequests.filter(r => r.status === "provided");

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6 fade-in-up">
      <div>
        <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Inbox</h2>
        <p className="text-sm text-slate-500 mt-1">
          {isContributorView
            ? `Documents the reviewers have requested from you — ${docRequests.length} request${docRequests.length !== 1 ? "s" : ""}.`
            : `Your work queue — ${assessments.length} total assessment${assessments.length !== 1 ? "s" : ""}.`}
        </p>
      </div>

      {loading ? (
        <div className="space-y-5">
          {[1, 2, 3].map(i => (
            <div key={i} className="space-y-2">
              <div className="skeleton h-3.5 w-24 rounded" />
              {[1, 2].map(j => <div key={j} className="skeleton h-14 rounded-xl" />)}
            </div>
          ))}
        </div>
      ) : isContributorView ? (
        <DocumentRequestsInbox requests={docRequests} onProvided={async () => load()} />
      ) : assessments.length === 0 ? (
        <div className="bg-white border border-slate-100 rounded-2xl py-16 text-center" style={{ boxShadow: "var(--shadow-card)" }}>
          <ClipboardCheck size={28} className="text-slate-200 mx-auto mb-4" />
          <p className="text-sm font-semibold text-slate-500 mb-1">Inbox is empty</p>
          <p className="text-xs text-slate-400">No assessments found. Create one to get started.</p>
        </div>
      ) : (
        <div className="space-y-6">
          {providedDocs.length > 0 && (
            <div>
              <div className="flex items-center gap-2 mb-3">
                <FileText size={14} className="text-slate-400" />
                <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider">Documents Provided by Contributors</h3>
                <span className="badge badge-neutral ml-auto">{providedDocs.length}</span>
              </div>
              <div className="space-y-2">
                {providedDocs.map(r => (
                  <div key={r.id}
                    onClick={() => { const a = assessments.find(x => x.id === r.assessment_id); if (a) setSelected(a); }}
                    className="bg-white border border-slate-100 rounded-xl px-4 py-3 flex items-center gap-3 hover:border-blue-200 hover:bg-blue-50/30 cursor-pointer transition-all group"
                    style={{ boxShadow: "var(--shadow-card)" }}>
                    <FileText size={14} className="text-blue-500 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <p className="text-sm font-semibold text-slate-900 truncate group-hover:text-blue-700 transition-colors">{r.evidence_type}</p>
                        {r.market_label && (
                          <span className="inline-flex items-center gap-0.5 text-[10px] font-semibold text-blue-600 bg-blue-50 rounded-full px-2 py-0.5 shrink-0">
                            <MapPin size={9} /> {r.market_label}
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-slate-400 truncate mt-0.5">
                        {r.assessment_name}
                        {r.provided_by && (
                          <span className="inline-flex items-center gap-1 ml-1 text-slate-500">
                            · <User size={10} /> {r.provided_by}
                          </span>
                        )}
                        {" "}· {(r.provided_files || []).length} file{(r.provided_files || []).length !== 1 ? "s" : ""} — review in Required Documents
                      </p>
                    </div>
                    <span className="badge badge-low shrink-0">Provided</span>
                    <ChevronRight size={14} className="text-slate-300 group-hover:text-blue-400 transition-colors shrink-0" />
                  </div>
                ))}
              </div>
            </div>
          )}
          <InboxSection title="Needs Scoring"  icon={Star}           items={needsScoring} onOpen={setSelected} emptyMsg="No assessments awaiting scoring." />
          <InboxSection title="Review Queue"  icon={ClipboardCheck} items={completed}    onOpen={setSelected} emptyMsg="No completed assessments to review." />
          <InboxSection title="Drafts"        icon={FileText}       items={drafts}       onOpen={setSelected} emptyMsg="No draft assessments." />
        </div>
      )}
    </div>
  );
}

