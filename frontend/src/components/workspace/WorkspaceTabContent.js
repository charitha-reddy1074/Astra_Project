"use client";

import { useState, useEffect, useCallback } from "react";
import {
  FileText, Upload, ClipboardList, Trash2,
  Sparkles, Mic, ShieldCheck, ClipboardCheck, Wand2, Star, AlertCircle,
  Send, Check, X, Inbox, Download, MapPin, User,
} from "lucide-react";
import { api } from "@/lib/api";
import { getIdentity } from "@/lib/auth";
import EvidencePreviewModal from "./EvidencePreviewModal";

// Required documents + reviewer-to-contributor requests (Change: doc request flow)
const REQUEST_STATUS = {
  requested: { badge: "badge-medium",  label: "Requested"      },
  provided:  { badge: "badge-low",     label: "Provided"       },
  accepted:  { badge: "badge-success", label: "Accepted"       },
  rejected:  { badge: "badge-high",    label: "Needs re-upload" },
};

export function RequiredDocumentsTab({ assessmentId, canRequest, canProvide, marketId }) {
  const [requirements, setRequirements] = useState(null);
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(() => new Set());
  const [note, setNote] = useState("");
  const [sending, setSending] = useState(false);
  const [busyId, setBusyId] = useState(null);

  // Preview-before-submit state for the contributor's provide flow.
  const [preview, setPreview] = useState(null); // { mode, reqId, files?, report? }
  const [submitting, setSubmitting] = useState(false);
  const [previewError, setPreviewError] = useState(null);

  const load = useCallback(async () => {
    try {
      const [reqs, dreqs] = await Promise.all([
        api.assessments.evidenceRequirements(assessmentId),
        api.assessments.documentRequests(assessmentId, marketId || null),
      ]);
      setRequirements(reqs);
      setRequests(dreqs);
    } catch (e) { console.error(e); } finally { setLoading(false); }
  }, [assessmentId, marketId]);
  useEffect(() => { load(); }, [load]);

  // Group expected evidence types by domain, deduped, with their controls.
  const domains = (() => {
    const map = new Map();
    for (const r of requirements?.requirements || []) {
      if (!map.has(r.domain_code))
        map.set(r.domain_code, { code: r.domain_code, name: r.domain_name, types: new Map() });
      const dom = map.get(r.domain_code);
      for (const t of r.expected_evidence_types || []) {
        if (!dom.types.has(t)) dom.types.set(t, { controls: [] });
        dom.types.get(t).controls.push(r.control_code);
      }
    }
    return Array.from(map.values());
  })();

  const requestedKeys = new Set(requests.map((r) => `${r.domain_code}|${r.evidence_type}`));
  const keyOf = (dom, type) => `${dom.code}|${type}`;

  const toggle = (key) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });

  const sendRequests = async () => {
    const items = [];
    for (const dom of domains) {
      for (const [type, info] of dom.types) {
        if (selected.has(keyOf(dom, type)))
          items.push({
            evidence_type: type, domain_code: dom.code, domain_name: dom.name,
            control_code: info.controls[0],
          });
      }
    }
    if (!items.length) return;
    setSending(true);
    try {
      await api.assessments.createDocumentRequests(assessmentId, items, getIdentity() || "Reviewer", note || null);
      setSelected(new Set()); setNote("");
      await load();
    } catch (e) { alert(e.message); } finally { setSending(false); }
  };

  // Contributor picks files, previews them, then uploads to the reviewer.
  const stageUpload = (requestId, fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    setPreviewError(null);
    setPreview({ mode: "upload", reqId: requestId, files });
  };

  // Contributor generates an immutable Prowler report, previews it, then submits.
  const generateProwler = async (requestId) => {
    setBusyId(requestId);
    setPreviewError(null);
    try {
      const report = await api.assessments.generateProwler(assessmentId, requestId);
      setPreview({ mode: "prowler", reqId: requestId, report });
    } catch (e) { alert(e.message); } finally { setBusyId(null); }
  };

  const submitPreview = async () => {
    if (!preview) return;
    setSubmitting(true);
    setPreviewError(null);
    try {
      if (preview.mode === "upload") {
        await api.assessments.provideDocuments(
          assessmentId, preview.reqId, preview.files, getIdentity() || null);
      } else {
        await api.assessments.submitProwler(
          assessmentId, preview.reqId, preview.report.report_id, getIdentity() || null);
      }
      setPreview(null);
      await load();
    } catch (e) { setPreviewError(e.message); } finally { setSubmitting(false); }
  };

  const review = async (requestId, action) => {
    const note = action === "reject"
      ? window.prompt("Why is this being rejected? (sent to the contributor)") : null;
    if (action === "reject" && note === null) return;
    setBusyId(requestId);
    try { await api.assessments.reviewDocumentRequest(assessmentId, requestId, action, note); await load(); }
    catch (e) { alert(e.message); } finally { setBusyId(null); }
  };

  const withdraw = async (requestId) => {
    if (!window.confirm("Withdraw this document request?")) return;
    setBusyId(requestId);
    try { await api.assessments.deleteDocumentRequest(assessmentId, requestId); await load(); }
    catch (e) { alert(e.message); } finally { setBusyId(null); }
  };

  // Group requests by market — for org owner / manager multi-market view
  const marketGroups = (() => {
    const groups = new Map();
    for (const r of requests) {
      const key = r.market_label || "__none";
      if (!groups.has(key)) groups.set(key, { label: r.market_label || null, items: [] });
      groups.get(key).items.push(r);
    }
    return [...groups.values()];
  })();
  const isMultiMarket = canRequest && (marketGroups.length > 1 || (marketGroups.length === 1 && marketGroups[0].label));

  const renderRequestCard = (r) => {
    const st = REQUEST_STATUS[r.status] || REQUEST_STATUS.requested;
    const busy = busyId === r.id;
    return (
      <div key={r.id} className="border border-slate-100 rounded-xl px-3 py-2.5 space-y-1.5">
        <div className="flex items-center gap-2 flex-wrap">
          {r.domain_code && <span className="badge badge-neutral font-mono text-[10px]">{r.domain_code}</span>}
          <span className="text-sm font-semibold text-slate-800 flex-1 min-w-0">{r.evidence_type}</span>
          <span className={`badge ${st.badge} shrink-0`}>{st.label}</span>
        </div>
        {r.note && <p className="text-[11px] text-slate-500">Note: {r.note}</p>}
        {r.status === "rejected" && r.review_note && (
          <p className="text-[11px] text-rose-600">Reviewer note: {r.review_note}</p>
        )}
        {(r.provided_files || []).length > 0 && (
          <div className="space-y-1">
            <div className="flex flex-wrap gap-1.5">
              {r.provided_files.map((f, i) => (
                <span key={i} className="badge badge-low text-[10px] flex items-center gap-1">
                  <FileText size={10} /> {f.original_name}
                </span>
              ))}
            </div>
            {r.provided_by && (
              <p className="text-[11px] text-slate-400 flex items-center gap-1">
                <User size={10} /> Uploaded by {r.provided_by}
              </p>
            )}
          </div>
        )}
        <div className="flex items-center gap-2 pt-0.5">
          {canProvide && (r.status === "requested" || r.status === "rejected") && (
            <>
              <label className="btn-secondary text-[11px] px-2.5 py-1 cursor-pointer">
                <Upload size={11} /> Upload evidence
                <input type="file" multiple className="hidden" disabled={busy}
                  onChange={(e) => { stageUpload(r.id, e.target.files); e.target.value = ""; }} />
              </label>
              <button onClick={() => generateProwler(r.id)} disabled={busy}
                className="inline-flex items-center gap-1 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1 text-[11px] font-semibold text-amber-700 transition-all hover:bg-amber-100 disabled:opacity-50">
                <ShieldCheck size={11} /> {busy ? "Generating…" : "Generate with Prowler"}
              </button>
            </>
          )}
          {canRequest && r.status === "provided" && (
            <>
              <button onClick={() => review(r.id, "accept")} disabled={busy}
                className="btn-primary text-[11px] px-2.5 py-1"><Check size={11} /> Accept</button>
              <button onClick={() => review(r.id, "reject")} disabled={busy}
                className="btn-secondary text-[11px] px-2.5 py-1"><X size={11} /> Reject</button>
            </>
          )}
          {canRequest && (
            <button onClick={() => withdraw(r.id)} disabled={busy} title="Withdraw request"
              className="p-1 rounded-lg text-slate-300 hover:text-red-600 hover:bg-red-50 transition-colors ml-auto">
              <Trash2 size={12} />
            </button>
          )}
        </div>
      </div>
    );
  };

// Required documents + reviewer-to-contributor requests (Change: doc request flow)

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-5">
      <div>
// Required documents + reviewer-to-contributor requests (Change: doc request flow)
        <p className="text-xs text-slate-500 mt-0.5">
          {canRequest
            ? "Documents this assessment expects, per domain. Select what you need and send a request to the contributor."
            : "Documents the reviewer needs for this assessment. Upload against each open request below."}
        </p>
      </div>

      {/* Requests — grouped by market when org owner / manager multi-market view */}
      {requests.length > 0 && (
        isMultiMarket ? (
          <div className="bg-white border border-slate-100 rounded-2xl p-4 space-y-4" style={{ boxShadow: "var(--shadow-card)" }}>
            <div className="flex items-center gap-2">
              <Inbox size={15} className="text-blue-600" />
              <p className="text-sm font-bold text-slate-800">Document Requests by Market</p>
              <span className="badge badge-neutral ml-auto">{requests.length}</span>
            </div>
            {marketGroups.map((g) => (
              <div key={g.label || "__none"} className="space-y-2">
                {g.label && (
                  <div className="flex items-center gap-2">
                    <MapPin size={11} className="text-blue-500 shrink-0" />
                    <span className="text-[11px] font-bold text-blue-700 uppercase tracking-wider">{g.label}</span>
                    <div className="flex-1 h-px bg-blue-100" />
                    <span className="text-[10px] text-slate-400">{g.items.length} request{g.items.length !== 1 ? "s" : ""}</span>
                  </div>
                )}
                <div className="space-y-2 pl-1">
                  {g.items.map(renderRequestCard)}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="bg-white border border-slate-100 rounded-2xl p-4 space-y-2.5" style={{ boxShadow: "var(--shadow-card)" }}>
            <div className="flex items-center gap-2">
              <Inbox size={15} className="text-blue-600" />
              <p className="text-sm font-bold text-slate-800">Document requests</p>
              <span className="badge badge-neutral ml-auto">{requests.length}</span>
            </div>
            {requests.map(renderRequestCard)}
          </div>
        )
      )}

      {/* Requirement checklist per domain */}
      {domains.map((dom) => (
        <div key={dom.code} className="bg-white border border-slate-100 rounded-2xl p-4 space-y-2" style={{ boxShadow: "var(--shadow-card)" }}>
          <div className="flex items-center gap-2">
            <span className="badge badge-neutral font-mono">{dom.code}</span>
            <p className="text-sm font-bold text-slate-800 truncate">{dom.name}</p>
            <span className="badge badge-neutral ml-auto">{dom.types.size}</span>
          </div>
          <div className="space-y-1">
            {Array.from(dom.types.entries()).map(([type, info]) => {
              const key = keyOf(dom, type);
              const already = requestedKeys.has(key);
              return (
                <label key={key}
                  className={`flex items-start gap-2.5 px-2.5 py-2 rounded-lg text-sm ${already ? "opacity-60" : canRequest ? "hover:bg-slate-50 cursor-pointer" : ""}`}>
                  {canRequest && (
                    <input type="checkbox" className="mt-0.5" checked={selected.has(key)}
                      disabled={already} onChange={() => toggle(key)} />
                  )}
                  <span className="flex-1 text-slate-700 leading-snug">{type}</span>
                  <span className="text-[10px] text-slate-400 font-mono shrink-0">{info.controls.join(", ")}</span>
                  {already && <span className="badge badge-low text-[10px] shrink-0">Requested</span>}
                </label>
              );
            })}
          </div>
        </div>
      ))}
      {domains.length === 0 && (
        <p className="text-xs text-slate-400">No evidence requirements found — generate the questionnaire first.</p>
      )}

      <EvidencePreviewModal
        open={preview !== null}
        onClose={() => { if (!submitting) { setPreview(null); setPreviewError(null); } }}
        mode={preview?.mode || "upload"}
        files={preview?.files || []}
        report={preview?.report || null}
        onSubmit={submitPreview}
        busy={submitting}
        error={previewError}
      />

      {/* Send request bar */}
      {canRequest && selected.size > 0 && (
        <div className="sticky bottom-4 bg-white border border-blue-200 rounded-2xl p-4 space-y-2.5" style={{ boxShadow: "var(--shadow-card)" }}>
          <input value={note} onChange={(e) => setNote(e.target.value)}
            placeholder="Optional note to the contributor (deadline, format, scope…)"
            className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:bg-white transition-all placeholder:text-slate-300" />
          <button onClick={sendRequests} disabled={sending} className="btn-primary text-xs w-full justify-center">
            <Send size={12} /> {sending ? "Sending…" : `Request ${selected.size} document${selected.size !== 1 ? "s" : ""} from contributor`}
          </button>
        </div>
      )}
    </div>
  );
}

// ── Engagement document buckets (Change 2) ───────────────────────────────────
const DOC_BUCKETS = [
  { key: "survey",     label: "Pre-Assessment Survey", icon: ClipboardCheck,
    accept: ".docx,.doc", hint: "Upload the completed pre-assessment survey (DOCX)." },
  { key: "transcript", label: "Interview Transcripts",  icon: Mic,
    accept: ".txt,.md,.docx,.doc,.pdf,.csv,.json,.xml,.log,.vtt,.srt,.rtf,.png,.jpg,.jpeg",
    hint: "Meeting / call recording transcripts — any text format or screenshot." },
  { key: "evidence",   label: "Evidence Files",         icon: FileText,
    accept: ".pdf,.docx,.xlsx,.xls,.txt,.csv,.md,.json,.xml,.log,.png,.jpg,.jpeg",
    hint: "Supporting evidence artefacts (logs, reports, configs, screenshots)." },
  { key: "policy",     label: "Security Policies",      icon: ShieldCheck,
    accept: ".pdf,.docx,.doc,.txt,.md,.png,.jpg,.jpeg", hint: "Security policy & standard documents." },
];

const VERDICT = {
  strong:  { badge: "badge-success",  label: "Strong"  },
  partial: { badge: "badge-medium",   label: "Partial" },
  weak:    { badge: "badge-high",     label: "Weak"    },
};
const ratingBar = (s) => (s >= 75 ? "bg-emerald-500" : s >= 40 ? "bg-amber-400" : "bg-rose-500");

const cleanName = (n) => (n || "").replace(/^[0-9a-f-]{36}_/i, "");

export function EngagementDocumentsTab({ assessmentId, canUpload, preAssessmentFileName, marketId = null }) {
  const [data, setData] = useState({ by_type: {}, counts: {}, expected_by_category: [], doc_types: [] });
  const [loading, setLoading] = useState(true);
  const [uploadingKey, setUploadingKey] = useState(null);
  const [deletingName, setDeletingName] = useState(null);
  const [showExpected, setShowExpected] = useState(false);

  const load = useCallback(async () => {
    try { setData(await api.assessments.engagementDocuments(assessmentId, marketId || null)); }
    catch (e) { console.error(e); } finally { setLoading(false); }
  }, [assessmentId, marketId]);
  useEffect(() => { load(); }, [load]);

  const handleFiles = async (bucketKey, fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    setUploadingKey(bucketKey);
    try { await api.assessments.uploadEvidenceBulk(assessmentId, files, bucketKey, marketId || null); await load(); }
    catch (e) { alert(e.message); } finally { setUploadingKey(null); }
  };

  const handleDelete = async (storedName, original) => {
    if (!window.confirm(`Delete "${original || cleanName(storedName)}"? This cannot be undone.`)) return;
    setDeletingName(storedName);
    try { await api.assessments.deleteEvidence(assessmentId, storedName, marketId || null); await load(); }
    catch (e) { alert(e.message); } finally { setDeletingName(null); }
  };

  const downloadUrl = preAssessmentFileName
    ? api.assessments.preAssessmentDownloadUrl(assessmentId)
    : api.assessments.defaultPreAssessmentUrl();

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-5">
      <div>
        <h3 className="text-base font-bold text-slate-900">Engagement Documents</h3>
        <p className="text-xs text-slate-500 mt-0.5">
          Upload the documents collected for this engagement. The <b>AI Assistance</b> tab reads
          these to pre-fill answers and rate the evidence.
        </p>
      </div>

      {/* Pre-assessment questionnaire download */}
      <div className="bg-blue-50 border border-blue-100 rounded-2xl p-4 flex items-start gap-3">
        <Download size={16} className="text-blue-500 mt-0.5 shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-bold text-slate-800 mb-0.5">Pre-Assessment Questionnaire</p>
          {preAssessmentFileName ? (
            <p className="text-xs text-slate-500 mb-2 truncate">
              Custom file: <span className="font-medium text-slate-700">{preAssessmentFileName}</span>
            </p>
          ) : (
            <p className="text-xs text-slate-500 mb-2">No custom questionnaire — the default template will be downloaded.</p>
          )}
          <a
            href={downloadUrl}
            download
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-white border border-blue-200 rounded-lg text-xs font-semibold text-blue-700 hover:bg-blue-100 transition-colors"
          >
            <Download size={12} />
            {preAssessmentFileName ? "Download Questionnaire" : "Download Default Template"}
          </a>
          <p className="text-[10px] text-slate-400 mt-2">
            Fill this out and upload the completed file in the <b>Pre-Assessment Survey</b> section below.
          </p>
        </div>
      </div>

      {DOC_BUCKETS.map((b) => {
        const Icon = b.icon;
        const docs = (data.by_type && data.by_type[b.key]) || [];
        const isEvidence = b.key === "evidence";
        return (
          <div key={b.key} className="bg-white border border-slate-100 rounded-2xl p-4 space-y-3" style={{ boxShadow: "var(--shadow-card)" }}>
            <div className="flex items-center gap-2">
              <Icon size={16} className="text-blue-600 shrink-0" />
              <p className="text-sm font-bold text-slate-800">{b.label}</p>
              <span className="badge badge-neutral ml-auto">{docs.length}</span>
            </div>
            <p className="text-[11px] text-slate-400">{b.hint}</p>

            {canUpload && (
              <label className="block border-2 border-dashed border-slate-200 rounded-xl py-4 text-center cursor-pointer hover:border-blue-400 transition-colors">
                <Upload size={16} className="text-blue-500 mx-auto mb-1" />
                <p className="text-xs font-semibold text-slate-600">
                  {uploadingKey === b.key ? "Uploading…" : `Upload ${b.label.toLowerCase()}`}
                </p>
                <p className="text-[10px] text-slate-400 mt-0.5">Accepted: {b.accept.replace(/\./g, " ").trim()}</p>
                <input type="file" multiple className="hidden" disabled={uploadingKey === b.key}
                  accept={b.accept}
                  onChange={(e) => { handleFiles(b.key, e.target.files); e.target.value = ""; }} />
              </label>
            )}

            {docs.length > 0 && (
              <div className="space-y-1.5">
                {docs.map((doc, i) => (
                  <div key={i} className="border border-slate-100 rounded-lg px-3 py-2 flex items-center gap-2.5">
                    <FileText size={13} className="text-blue-500 shrink-0" />
                    <span className="text-sm text-slate-700 truncate flex-1">{doc.original_name || cleanName(doc.stored_name)}</span>
                    <span className="badge badge-success shrink-0">Ingested</span>
                    {canUpload && (
                      <button onClick={() => handleDelete(doc.stored_name, doc.original_name)} disabled={deletingName === doc.stored_name}
                        title="Delete" className="p-1 rounded-lg text-slate-300 hover:text-red-600 hover:bg-red-50 transition-colors disabled:opacity-40 shrink-0">
                        <Trash2 size={13} />
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* What the evidence must contain — per category */}
            {isEvidence && (data.expected_by_category || []).length > 0 && (
              <div className="pt-1">
                <button onClick={() => setShowExpected((s) => !s)}
                  className="flex items-center gap-1.5 text-[10px] font-bold text-slate-400 uppercase tracking-wider hover:text-slate-600">
                  <ClipboardList size={12} /> What this evidence should contain · {data.expected_by_category.length} categories {showExpected ? "▾" : "▸"}
                </button>
                {showExpected && (
                  <div className="space-y-1.5 mt-2 max-h-80 overflow-y-auto pr-1">
                    {data.expected_by_category.map((c, i) => (
                      <div key={i} className="bg-slate-50 border border-slate-100 rounded-lg px-3 py-2">
                        <div className="flex items-center gap-1.5 mb-1 flex-wrap">
                          <span className="badge badge-neutral font-mono text-[10px]">{c.domain_code}</span>
                          <span className="text-xs font-semibold text-slate-700">{c.category_name}</span>
                        </div>
                        {c.criteria_statement && <p className="text-[11px] text-slate-500 leading-snug mb-1">{c.criteria_statement}</p>}
                        {(c.expected_evidence_types || []).length > 0 && (
                          <div className="flex flex-wrap gap-1">
                            {c.expected_evidence_types.map((t, j) => <span key={j} className="badge badge-low text-[10px]">{t}</span>)}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
      {loading && <p className="text-xs text-slate-400">Loading…</p>}
    </div>
  );
}

export function AiAssistanceTab({ assessmentId, canAnswer, onPrefill, prefilling, onChanged, marketId = null }) {
  const [results, setResults] = useState({ ratings: [], followups: [] });
  const [loading, setLoading] = useState(true);
  const [rating, setRating] = useState(false);
  const [msg, setMsg] = useState(null);

  const load = useCallback(async () => {
    try { setResults(await api.assessments.aiResults(assessmentId, marketId || null)); }
    catch (e) { console.error(e); } finally { setLoading(false); }
  }, [assessmentId, marketId]);
  useEffect(() => { load(); }, [load]);

  const handleRate = async () => {
    setRating(true); setMsg(null);
    try {
      const res = await api.assessments.aiRateControls(assessmentId, marketId || null);
      if (res.ok === false) { setMsg(res.error || "Rating is unavailable."); return; }
      const s = res.summary || {};
      if (!res.ratings?.length) { setMsg(s.message || "No ratings produced."); }
      await load();
      onChanged?.();  // refresh Conduct so the new follow-up questions appear
      if (res.ratings?.length) {
        setMsg(`Rated ${s.categories_rated || res.ratings.length} categories · generated ${s.questions_generated || 0} follow-up questions.`);
      }
    } catch (e) { setMsg(e.message); } finally { setRating(false); }
  };

  const ratings = results.ratings || [];

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-5">
      {/* Action 1: pre-fill */}
      <div className="bg-white border border-slate-100 rounded-2xl p-5" style={{ boxShadow: "var(--shadow-card)" }}>
        <div className="flex items-center gap-2 mb-1.5">
          <Sparkles size={16} className="text-blue-600" />
          <p className="text-sm font-bold text-slate-800">Pre-fill answers from documents</p>
        </div>
        <p className="text-xs text-slate-500 mb-3">
          Drafts answers in the <b>Conduct</b> tab strictly from the engagement documents you uploaded.
        </p>
        <button onClick={onPrefill} disabled={prefilling || !canAnswer} className="btn-secondary text-xs">
          <Sparkles size={13} /> {prefilling ? "Pre-filling…" : "Pre-fill answers"}
        </button>
      </div>

      {/* Action 2: rate + generate */}
      <div className="bg-white border border-slate-100 rounded-2xl p-5" style={{ boxShadow: "var(--shadow-card)" }}>
        <div className="flex items-center gap-2 mb-1.5">
          <Wand2 size={16} className="text-blue-600" />
          <p className="text-sm font-bold text-slate-800">Rate evidence &amp; generate follow-up questions</p>
        </div>
        <p className="text-xs text-slate-500 mb-3">
          Rates the uploaded documents for each category against its criteria, then generates
          <b> follow-up questions per category — as many as the gaps warrant (up to 10)</b> —
          probing evidence genuinity, policy backing, and real-world implementation
          (appended at the end of that category in the Conduct tab).
        </p>
        <button onClick={handleRate} disabled={rating || !canAnswer} className="btn-primary text-xs">
          <Wand2 size={13} /> {rating ? "Analysing evidence…" : "Rate controls & generate questions"}
        </button>
        {msg && <p className="text-xs text-slate-500 mt-2 flex items-start gap-1.5"><AlertCircle size={13} className="text-amber-500 mt-0.5 shrink-0" />{msg}</p>}
      </div>

      {/* Ratings detail */}
      <div>
        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2">
          {ratings.length} categor{ratings.length !== 1 ? "ies" : "y"} rated
        </p>
        {loading ? (
          <p className="text-xs text-slate-400">Loading…</p>
        ) : ratings.length === 0 ? (
          <div className="bg-white border border-slate-100 rounded-2xl py-10 text-center" style={{ boxShadow: "var(--shadow-card)" }}>
            <Star size={22} className="text-slate-200 mx-auto mb-2" />
            <p className="text-sm text-slate-400">No ratings yet — upload documents, then run the analysis above.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {ratings.map((r, i) => {
              const v = VERDICT[r.verdict] || VERDICT.partial;
              const score = Math.round(r.score || 0);
              return (
                <div key={i} className="bg-white border border-slate-100 rounded-2xl p-4 space-y-2.5" style={{ boxShadow: "var(--shadow-card)" }}>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="badge badge-neutral font-mono text-[10px]">{r.domain_code}</span>
                    <span className="text-sm font-bold text-slate-800">{r.category_name}</span>
                    <span className={`badge ${v.badge} ml-auto`}>{v.label}</span>
                    <span className="text-sm font-bold text-slate-900 tabular-nums w-8 text-right">{score}</span>
                  </div>
                  <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                    <div className={`h-full rounded-full ${ratingBar(score)}`} style={{ width: `${score}%` }} />
                  </div>
                  {r.criteria_statement && (
                    <p className="text-[11px] text-slate-500 leading-snug border-l-2 border-blue-200 pl-2.5 bg-blue-50/40 py-1.5 rounded-r-lg">
                      <span className="font-semibold text-slate-600">Criteria: </span>{r.criteria_statement}
                    </p>
                  )}
                  {r.rationale && <p className="text-xs text-slate-600 leading-relaxed">{r.rationale}</p>}
                  {(r.missing || []).length > 0 && (
                    <div>
                      <p className="text-[10px] font-bold text-rose-500 uppercase tracking-wider mb-1">Gaps</p>
                      <ul className="list-disc pl-4 space-y-0.5">
                        {r.missing.map((m, j) => <li key={j} className="text-xs text-rose-700/90 leading-snug">{m}</li>)}
                      </ul>
                    </div>
                  )}
                  {(r.followups || []).length > 0 && (
                    <div>
                      <p className="text-[10px] font-bold text-blue-500 uppercase tracking-wider mb-1">Follow-up questions added to Conduct</p>
                      <ol className="list-decimal pl-4 space-y-0.5">
                        {r.followups.map((q, j) => <li key={j} className="text-xs text-slate-600 leading-snug">{q}</li>)}
                      </ol>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}


