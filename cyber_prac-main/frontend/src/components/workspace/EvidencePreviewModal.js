"use client";

// Preview-before-submit dialog for the evidence collection workflow.
//
// The owner never submits blind: whether they uploaded files or generated a
// Prowler report, they see exactly what will be sent to the assessor first, then
// confirm. Generated Prowler reports are read-only ("immutable") — the owner can
// submit or discard, never edit.
//
// Follows the shared Modal shell + gold/red theme used across the workspace.

import { useEffect, useState } from "react";
import { FileText, Upload, ShieldCheck, Send, X, Loader2, Lock } from "lucide-react";
import { Modal } from "./UserModals";

const TEXT_EXTS = [".txt", ".csv", ".json", ".md", ".log", ".yaml", ".yml", ".ini", ".conf"];
const PREVIEW_LIMIT = 6000;

function isTextLike(file) {
  if (file.type && file.type.startsWith("text/")) return true;
  const name = (file.name || "").toLowerCase();
  return TEXT_EXTS.some((ext) => name.endsWith(ext));
}

function fmtSize(bytes) {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function EvidencePreviewModal({
  open, onClose, mode, files = [], report = null, onSubmit, busy = false, error = null,
}) {
  const [previews, setPreviews] = useState([]);

  // Read text-like uploads client-side so the owner can eyeball the content.
  useEffect(() => {
    if (!open || mode !== "upload") return;
    let cancelled = false;
    (async () => {
      const out = await Promise.all(
        Array.from(files).map(async (f) => {
          let text = null;
          if (isTextLike(f)) {
            try {
              const raw = await f.text();
              text = raw.slice(0, PREVIEW_LIMIT);
            } catch { text = null; }
          }
          return { name: f.name, size: f.size, text };
        })
      );
      if (!cancelled) setPreviews(out);
    })();
    return () => { cancelled = true; };
  }, [open, mode, files]);

  const isProwler = mode === "prowler";

  return (
    <Modal open={open} onClose={busy ? () => {} : onClose} width="max-w-3xl">
      <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div className="flex items-center gap-3">
          <span className={`flex h-9 w-9 items-center justify-center rounded-xl ${
            isProwler ? "bg-amber-50 text-amber-600" : "bg-blue-50 text-blue-600"}`}>
            {isProwler ? <ShieldCheck size={16} /> : <Upload size={16} />}
          </span>
          <div>
            <h3 className="text-sm font-bold text-slate-900">
              {isProwler ? "Review generated Prowler report" : "Review before submitting"}
            </h3>
            <p className="text-[11px] text-slate-400">
              {isProwler
                ? "This report was generated once and is read-only. Submit it or discard it."
                : "Confirm the files below are correct — they will be sent to the assessor."}
            </p>
          </div>
        </div>
        <button onClick={busy ? undefined : onClose}
          className="rounded-lg p-1 text-slate-300 transition-colors hover:bg-slate-50 hover:text-slate-600">
          <X size={16} />
        </button>
      </div>

      <div className="max-h-[55vh] overflow-y-auto px-5 py-4">
        {isProwler && report && (
          <div>
            <div className="mb-2.5 flex items-center gap-2">
              <FileText size={13} className="shrink-0 text-amber-600" />
              <span className="truncate text-xs font-semibold text-slate-700">{report.filename}</span>
              <span className="badge badge-medium shrink-0">
                <Lock size={9} /> Generated · read-only
              </span>
            </div>
            <pre className="max-h-[42vh] overflow-auto rounded-xl border border-slate-100 bg-slate-50 p-3.5 font-mono text-[11px] leading-relaxed text-slate-700 whitespace-pre-wrap">
              {report.content}
            </pre>
          </div>
        )}

        {!isProwler && (
          <div className="space-y-3">
            {previews.length === 0 && (
              <p className="text-xs text-slate-400">No files selected.</p>
            )}
            {previews.map((p, i) => (
              <div key={i} className="rounded-xl border border-slate-100 bg-white p-3"
                style={{ boxShadow: "var(--shadow-card)" }}>
                <div className="flex items-center gap-2">
                  <FileText size={13} className="shrink-0 text-blue-500" />
                  <span className="truncate text-xs font-semibold text-slate-700">{p.name}</span>
                  <span className="ml-auto shrink-0 text-[10px] text-slate-400">{fmtSize(p.size)}</span>
                </div>
                {p.text != null ? (
                  <pre className="mt-2 max-h-40 overflow-auto rounded-lg border border-slate-100 bg-slate-50 p-2.5 font-mono text-[10.5px] leading-relaxed text-slate-600 whitespace-pre-wrap">
                    {p.text}
                    {p.text.length >= PREVIEW_LIMIT ? "\n… (truncated for preview)" : ""}
                  </pre>
                ) : (
                  <p className="mt-2 text-[11px] text-slate-400">
                    Preview not available for this file type — it will be submitted as-is.
                  </p>
                )}
              </div>
            ))}
          </div>
        )}

        {error && (
          <p className="mt-3 flex items-center gap-1.5 text-[11px] font-medium text-rose-600">
            <X size={11} /> {error}
          </p>
        )}
      </div>

      <div className="flex items-center justify-end gap-2.5 border-t border-slate-100 bg-slate-50 px-5 py-3.5">
        <button onClick={onClose} disabled={busy} className="btn-secondary disabled:opacity-50">
          {isProwler ? "Discard" : "Cancel"}
        </button>
        <button onClick={onSubmit} disabled={busy || (!isProwler && previews.length === 0)}
          className="btn-primary flex items-center gap-1.5 disabled:opacity-50">
          {busy ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
          {busy ? "Submitting…" : "Submit to assessor"}
        </button>
      </div>
    </Modal>
  );
}
