"use client";

import { useRef, useState } from "react";
import {
  Upload, Check, X, Clock, FileText, BookOpen, ChevronDown, Loader2, AlertCircle,
  ShieldCheck,
} from "lucide-react";
import { api } from "@/lib/api";
import { getIdentity } from "@/lib/auth";
import EvidencePreviewModal from "./EvidencePreviewModal";

// An owner is a contributor: for each request they see WHAT was asked for, the
// framework guidance explaining what will satisfy it, and an upload control.
// Never scores, findings or reports.
export const REQUEST_STATUS = {
  requested: { badge: "badge-medium",  label: "Action needed",   icon: Clock,       tone: "text-amber-700"   },
  rejected:  { badge: "badge-high",    label: "Re-upload needed", icon: AlertCircle, tone: "text-rose-700"    },
  provided:  { badge: "badge-low",     label: "Awaiting review", icon: Clock,       tone: "text-blue-700"    },
  accepted:  { badge: "badge-success", label: "Accepted",        icon: Check,       tone: "text-emerald-700" },
};

export const NEEDS_ACTION = new Set(["requested", "rejected"]);

function Guidance({ guidance, evidenceType }) {
  const g = guidance || {};
  const hasControl = g.source === "control";
  const related = g.related_controls || [];
  const expected = g.expected_evidence_types || [];

  return (
    <div className="mt-3 rounded-xl border border-blue-100 bg-blue-50/60 p-3.5 space-y-3">
      <p className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-blue-700">
        <BookOpen size={11} className="shrink-0" /> How to answer this
      </p>

      {g.how_to_respond && (
        <p className="text-xs leading-relaxed text-slate-700">{g.how_to_respond}</p>
      )}

      {hasControl && (
        <div className="rounded-lg border border-blue-100 bg-white p-3">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
            {[g.framework, g.domain_name, g.category_name].filter(Boolean).join(" › ")}
          </p>
          <p className="mt-1 text-xs font-bold text-slate-900">
            {g.control_code}{g.control_name ? ` — ${g.control_name}` : ""}
          </p>
          {g.control_statement && (
            <p className="mt-1.5 text-xs leading-relaxed text-slate-600">{g.control_statement}</p>
          )}
          {g.criteria_statement && (
            <p className="mt-2 border-t border-slate-100 pt-2 text-[11px] leading-relaxed text-slate-500">
              <span className="font-semibold text-slate-600">What is assessed: </span>
              {g.criteria_statement}
            </p>
          )}
        </div>
      )}

      {!hasControl && related.length > 0 && (
        <div className="space-y-2">
          <p className="text-[11px] font-semibold text-slate-600">
            Controls this evidence supports{g.framework ? ` (${g.framework})` : ""}:
          </p>
          {related.map((c) => (
            <div key={c.control_code} className="rounded-lg border border-blue-100 bg-white p-2.5">
              <p className="text-xs font-bold text-slate-900">
                {c.control_code}{c.control_name ? ` — ${c.control_name}` : ""}
              </p>
              {c.control_statement && (
                <p className="mt-1 text-[11px] leading-relaxed text-slate-600">{c.control_statement}</p>
              )}
            </div>
          ))}
        </div>
      )}

      {expected.length > 0 && (
        <div>
          <p className="mb-1.5 text-[11px] font-semibold text-slate-600">Expected evidence</p>
          <ul className="space-y-1">
            {expected.map((e, i) => (
              <li key={i} className="flex gap-1.5 text-[11px] leading-relaxed text-slate-600">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-blue-400" />
                <span>{e}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!g.how_to_respond && !hasControl && !related.length && !expected.length && (
        <p className="text-xs leading-relaxed text-slate-600">
          No framework guidance is linked to this request. Provide the artefact described
          above{evidenceType ? ` (${evidenceType})` : ""} — if you are unsure what will
          satisfy it, ask the assessor who raised it.
        </p>
      )}
    </div>
  );
}

export default function OwnerRequestCard({ request, onProvided }) {
  const cfg = REQUEST_STATUS[request.status] || REQUEST_STATUS.requested;
  const StatusIcon = cfg.icon;
  const actionable = NEEDS_ACTION.has(request.status);

  const [open, setOpen] = useState(actionable);
  const [busy, setBusy] = useState(false);          // generating (Prowler)
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const fileRef = useRef(null);

  // Preview-before-submit state.
  const [previewMode, setPreviewMode] = useState(null); // "upload" | "prowler" | null
  const [pendingFiles, setPendingFiles] = useState([]);
  const [prowlerReport, setProwlerReport] = useState(null);

  // Step 1: owner picks files — stage them for preview, don't submit yet.
  const stageUpload = (fileList) => {
    const files = Array.from(fileList || []);
    if (fileRef.current) fileRef.current.value = "";
    if (!files.length) return;
    setError(null);
    setPendingFiles(files);
    setPreviewMode("upload");
  };

  // Step 1 (Prowler): generate an immutable report, then preview it.
  const generateProwler = async () => {
    setBusy(true);
    setError(null);
    try {
      const rep = await api.assessments.generateProwler(request.assessment_id, request.id);
      setProwlerReport(rep);
      setPreviewMode("prowler");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const closePreview = () => {
    if (submitting) return;
    setPreviewMode(null);
    setPendingFiles([]);
    setProwlerReport(null);
    setError(null);
  };

  // Step 2: owner confirms — submit the staged upload or generated report.
  const submitPreview = async () => {
    setSubmitting(true);
    setError(null);
    try {
      if (previewMode === "upload") {
        await api.assessments.provideDocuments(
          request.assessment_id, request.id, pendingFiles, getIdentity() || null,
        );
      } else if (previewMode === "prowler") {
        await api.assessments.submitProwler(
          request.assessment_id, request.id, prowlerReport.report_id, getIdentity() || null,
        );
      }
      setPreviewMode(null);
      setPendingFiles([]);
      setProwlerReport(null);
      onProvided?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-4"
      style={{ boxShadow: "var(--shadow-card)" }}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-bold leading-snug text-slate-900">
            {request.note || request.evidence_type}
          </p>
          <p className="mt-0.5 truncate text-[11px] text-slate-400">
            {[request.evidence_type !== request.note ? request.evidence_type : null,
              request.domain_name, request.control_code]
              .filter(Boolean).join(" · ") || "Requested evidence"}
          </p>
        </div>
        <span className={`badge ${cfg.badge} shrink-0`}>
          <StatusIcon size={10} /> {cfg.label}
        </span>
      </div>

      {request.status === "rejected" && request.review_note && (
        <p className="mt-2.5 rounded-lg border border-rose-100 bg-rose-50 px-3 py-2 text-[11px] leading-relaxed text-rose-700">
          <span className="font-semibold">Assessor feedback: </span>{request.review_note}
        </p>
      )}

      {(request.provided_files || []).length > 0 && (
        <div className="mt-2.5 space-y-1">
          <p className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
            You provided
          </p>
          {request.provided_files.map((f) => (
            <p key={f.stored_name} className="flex items-center gap-1.5 truncate text-[11px] text-slate-600">
              <FileText size={11} className="shrink-0 text-slate-300" /> {f.original_name}
            </p>
          ))}
        </div>
      )}

      <button onClick={() => setOpen((v) => !v)}
        className="mt-3 flex items-center gap-1 text-[11px] font-semibold text-blue-600 transition-colors hover:text-blue-800">
        <ChevronDown size={12} className={`transition-transform ${open ? "rotate-180" : ""}`} />
        {open ? "Hide guidance" : "What should I provide?"}
      </button>

      {open && <Guidance guidance={request.guidance} evidenceType={request.evidence_type} />}

      {error && (
        <p className="mt-2 flex items-center gap-1.5 text-[11px] font-medium text-rose-600">
          <X size={11} /> {error}
        </p>
      )}

      {request.status !== "accepted" && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input ref={fileRef} type="file" multiple className="hidden"
            onChange={(e) => stageUpload(e.target.files)} />
          <button disabled={busy || submitting} onClick={() => fileRef.current?.click()}
            className={`flex items-center gap-1.5 rounded-xl px-3.5 py-2 text-xs font-semibold transition-all disabled:opacity-50 ${
              actionable
                ? "brand-tile text-white"
                : "border border-slate-200 bg-white text-slate-700 hover:border-brand-pink hover:text-brand-orange"
            }`}>
            <Upload size={13} />
            {actionable ? "Upload evidence" : "Add more"}
          </button>
          <button disabled={busy || submitting} onClick={generateProwler}
            className="flex items-center gap-1.5 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2 text-xs font-semibold text-amber-700 transition-all hover:border-amber-300 hover:bg-amber-100 disabled:opacity-50">
            {busy ? <Loader2 size={13} className="animate-spin" /> : <ShieldCheck size={13} />}
            {busy ? "Generating…" : "Generate with Prowler"}
          </button>
          {request.status === "provided" && (
            <span className="text-[11px] text-slate-400">Sent to the assessor for review.</span>
          )}
        </div>
      )}

      <EvidencePreviewModal
        open={previewMode !== null}
        onClose={closePreview}
        mode={previewMode || "upload"}
        files={pendingFiles}
        report={prowlerReport}
        onSubmit={submitPreview}
        busy={submitting}
        error={previewMode ? error : null}
      />
    </div>
  );
}
