"use client";

// Modals & slide-over used by the admin People Directory (OwnersView).
//   • Modal            — shared centred dialog shell
//   • ConfirmDialog    — destructive-action confirmation
//   • UserFormModal    — create / edit a person
//   • UserDetailDrawer — right-hand profile panel with contact + workload

import { useState, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";
import {
  X, UserPlus, Save, AlertTriangle, Mail, Phone, MapPin, Building2, BadgeCheck,
  ClipboardCheck, FileText, Copy, Check, Shield, Trash2, Power, Pencil, Clock,
} from "lucide-react";
import { ROLE_META, ASSIGNABLE_ROLES, CONTRIBUTOR_ROLES, initials, avatarTint, copyText, mailtoUrl, organizationLabelOf, resolveOrganization } from "@/lib/users";

// Verbatim from the original OwnersView form — every input on this screen uses it.
export const INPUT_CLS =
  "w-full text-sm border border-slate-200 rounded-xl px-4 py-2.5 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-50 transition-all text-slate-900 placeholder:text-slate-400 bg-white";
const INPUT_DISABLED = `${INPUT_CLS} disabled:bg-slate-50 disabled:text-slate-400`;
const LABEL_CLS = "block text-xs font-bold text-slate-600 mb-1.5 uppercase tracking-wider";

// ── Shared shell ─────────────────────────────────────────────────────────────

/**
 * Every workspace view roots itself in `.fade-in-up`, whose `animation-fill-mode:
 * both` leaves a `transform` on the element — which makes it the containing block
 * for `position: fixed` children. Overlays must therefore be portalled to <body>
 * or they get clipped to the scrolling content column instead of the viewport.
 */
export function Portal({ children }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => { setMounted(true); }, []);
  if (!mounted || typeof document === "undefined") return null;
  return createPortal(children, document.body);
}

export function Modal({ open, onClose, children, width = "max-w-2xl" }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <Portal>
      <div className="fixed inset-0 bg-black/30 flex items-start justify-center z-50 p-4 overflow-y-auto" onClick={onClose}>
        <div className={`bg-white rounded-2xl w-full ${width} my-8`} style={{ boxShadow: "var(--shadow-elevated)" }}
          onClick={(e) => e.stopPropagation()}>
          {children}
        </div>
      </div>
    </Portal>
  );
}

// ── Confirm ──────────────────────────────────────────────────────────────────

export function ConfirmDialog({ open, title, message, confirmLabel = "Confirm", tone = "danger", busy, onConfirm, onClose }) {
  const toneCls = tone === "danger"
    ? "bg-rose-600 hover:bg-rose-700"
    : "bg-amber-600 hover:bg-amber-700";
  return (
    <Modal open={open} onClose={busy ? () => {} : onClose} width="max-w-sm">
      <div className="p-5 space-y-4">
        <div className="flex items-start gap-3">
          <div className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 ${tone === "danger" ? "bg-rose-50" : "bg-amber-50"}`}>
            <AlertTriangle size={16} className={tone === "danger" ? "text-rose-600" : "text-amber-600"} />
          </div>
          <div className="min-w-0">
            <h3 className="text-sm font-bold text-slate-900">{title}</h3>
            <p className="text-xs text-slate-500 mt-1 leading-relaxed">{message}</p>
          </div>
        </div>
        <div className="flex justify-end gap-2 pt-1">
          <button onClick={onClose} disabled={busy} className="btn-secondary text-xs">Cancel</button>
          <button onClick={onConfirm} disabled={busy}
            className={`text-xs font-semibold text-white px-4 py-2 rounded-xl transition-colors disabled:opacity-50 ${toneCls}`}>
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ── Create / Edit ────────────────────────────────────────────────────────────

const BLANK = {
  name: "", email: "", role: "auditor", organization: "",
  phone: "", department: "", job_title: "", notes: "", is_active: true,
};

export function UserFormModal({ open, user, organizations = [], onClose, onSubmit }) {
  const editing = Boolean(user);
  const [form, setForm]     = useState(BLANK);
  const [error, setError]   = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setSaving(false);
    // Stored organizations may be an id or a label; the <option> values are
    // ids, so resolve before seeding the select.
    setForm(user
      ? {
          name: user.name || "", email: user.email || "", role: user.role || "auditor",
          organization: resolveOrganization(user.organization, organizations)?.id || "",
          phone: user.phone || "", department: user.department || "",
          job_title: user.job_title || "", notes: user.notes || "", is_active: user.is_active !== false,
        }
      : BLANK);
  }, [open, user, organizations]);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const isContributor   = CONTRIBUTOR_ROLES.includes(form.role);
  const orgRequired     = isContributor;
  const organizationShown = isContributor || form.role === "auditor";

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    if (!form.name.trim())  return setError("Name is required.");
    if (!form.email.trim()) return setError("Email is required.");
    if (orgRequired && !form.organization) return setError("Contributors must be assigned to an organization.");
    setSaving(true);
    try {
      await onSubmit({
        ...form,
        name:         form.name.trim(),
        email:        form.email.trim().toLowerCase(),
        organization: isContributor ? (form.organization || null) : null,
        phone:        form.phone.trim()      || null,
        department:   form.department.trim() || null,
        job_title:    form.job_title.trim()  || null,
        notes:        form.notes.trim()      || null,
      });
    } catch (err) {
      setError(err?.message || "Something went wrong.");
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={saving ? () => {} : onClose}>
      <form onSubmit={submit}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl bg-blue-50 flex items-center justify-center">
              {editing ? <Pencil size={14} className="text-blue-600" /> : <UserPlus size={15} className="text-blue-600" />}
            </div>
            <div>
              <h3 className="text-sm font-bold text-slate-900">{editing ? "Edit person" : "Add a person"}</h3>
              <p className="text-[11px] text-slate-400">
                {editing ? user.email : "They will appear in the directory immediately."}
              </p>
            </div>
          </div>
          <button type="button" onClick={onClose} className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-100 transition-colors">
            <X size={16} />
          </button>
        </div>

        <div className="p-5 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={LABEL_CLS}>Full name <span className="text-rose-500">*</span></label>
              <input value={form.name} onChange={set("name")} placeholder="Jane Doe" className={INPUT_CLS} autoFocus />
            </div>
            <div>
              <label className={LABEL_CLS}>Email <span className="text-rose-500">*</span></label>
              <input type="email" value={form.email} onChange={set("email")} placeholder="jane@company.com"
                disabled={editing} title={editing ? "Email is the account identifier and cannot be changed." : undefined}
                className={INPUT_DISABLED} />
              {editing && <p className="text-[10px] text-slate-400 mt-1">Email is the account identifier and cannot be changed.</p>}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={LABEL_CLS}>Access level <span className="text-rose-500">*</span></label>
              <select value={form.role} onChange={(e) => setForm((f) => ({ ...f, role: e.target.value, organization: "" }))}
                className={INPUT_CLS}>
                {ASSIGNABLE_ROLES.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
              </select>
              <p className="text-[10px] text-slate-400 mt-1">Contributors must be assigned to an organization.</p>
            </div>
            <div>
              <label className={LABEL_CLS}>
                Organization {orgRequired ? <span className="text-rose-500">*</span> : <span className="text-slate-300 normal-case font-normal tracking-normal">(optional)</span>}
              </label>
              <select value={form.organization} onChange={set("organization")} disabled={!organizationShown} className={INPUT_DISABLED}>
                <option value="">{organizationShown ? (orgRequired ? "Select organization…" : "No default organization") : "Not organization scoped"}</option>
                {organizations.map((o) => <option key={o.id} value={o.id}>{o.path}</option>)}
              </select>
              <p className="text-[10px] text-slate-400 mt-1">
                {isContributor   ? "The organization this Contributor provides evidence for."
                  : form.role === "auditor" ? "Default organization for this Auditor's assessments."
                  : "Managers operate across every organization."}
              </p>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className={LABEL_CLS}>Job title</label>
              <input value={form.job_title} onChange={set("job_title")} placeholder="IT Manager" className={INPUT_CLS} />
            </div>
            <div>
              <label className={LABEL_CLS}>Department</label>
              <input value={form.department} onChange={set("department")} placeholder="Cyber Risk" className={INPUT_CLS} />
            </div>
            <div>
              <label className={LABEL_CLS}>Phone</label>
              <input value={form.phone} onChange={set("phone")} placeholder="+1-555-0100" className={INPUT_CLS} />
            </div>
          </div>

          <div>
            <label className={LABEL_CLS}>Notes</label>
            <textarea value={form.notes} onChange={set("notes")} rows={2} placeholder="Context for other admins…"
              className={`${INPUT_CLS} resize-none`} />
          </div>

          {editing && (
            <label className="flex items-center gap-2.5 px-3.5 py-3 rounded-xl border border-slate-200 bg-slate-50/60 cursor-pointer">
              <input type="checkbox" checked={form.is_active}
                onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))}
                className="w-4 h-4 accent-blue-600" />
              <span className="text-xs font-semibold text-slate-700">Active</span>
              <span className="text-[11px] text-slate-400">Inactive people keep their history but are hidden from assignment pickers.</span>
            </label>
          )}

          {error && (
            <div className="flex items-start gap-2 px-3.5 py-2.5 rounded-xl bg-rose-50 border border-rose-200 text-rose-700 text-xs">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" /> {error}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 px-5 py-4 border-t border-slate-100 bg-slate-50/50 rounded-b-2xl">
          <button type="button" onClick={onClose} disabled={saving} className="btn-secondary text-xs">Cancel</button>
          <button type="submit" disabled={saving} className="btn-primary">
            {editing ? <Save size={14} /> : <UserPlus size={14} />}
            {saving ? "Saving…" : editing ? "Save changes" : "Create person"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ── Detail drawer ────────────────────────────────────────────────────────────

function CopyBtn({ value, title = "Copy" }) {
  const [done, setDone] = useState(false);
  const click = useCallback(async (e) => {
    e.stopPropagation();
    if (await copyText(value)) { setDone(true); setTimeout(() => setDone(false), 1400); }
  }, [value]);
  if (!value) return null;
  return (
    <button type="button" onClick={click} title={title}
      className="p-1 rounded-md text-slate-300 hover:text-blue-600 hover:bg-blue-50 transition-colors shrink-0">
      {done ? <Check size={12} className="text-emerald-600" /> : <Copy size={12} />}
    </button>
  );
}

function Field({ icon: Icon, label, value, children }) {
  return (
    <div className="flex items-start gap-2.5 px-4 py-3">
      <Icon size={13} className="text-slate-300 mt-0.5 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">{label}</p>
        {children ?? <p className="text-xs text-slate-700 mt-0.5 break-words">{value || <span className="text-slate-300">Not set</span>}</p>}
      </div>
    </div>
  );
}

export function UserDetailDrawer({ user, workload, organizations = [], onClose, onEdit, onToggleActive, onDelete }) {
  useEffect(() => {
    if (!user) return;
    const onKey = (e) => { if (e.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [user, onClose]);

  if (!user) return null;
  const meta         = ROLE_META[user.role] || {};
  const w            = workload || {};
  const active       = user.is_active !== false;
  const orgLabel     = organizationLabelOf(user.organization, organizations);
  const isContributor = CONTRIBUTOR_ROLES.includes(user.role);

  return (
    <Portal>
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative bg-white w-full max-w-md h-full overflow-y-auto flex flex-col"
        style={{ boxShadow: "var(--shadow-elevated)" }} onClick={(e) => e.stopPropagation()}>

        {/* Header */}
        <div className="px-5 py-5 border-b border-slate-100 shrink-0">
          <div className="flex items-start justify-between mb-4">
            <span className={`badge ${active ? "badge-success" : "badge-neutral"}`}>{active ? "Active" : "Inactive"}</span>
            <button onClick={onClose} className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-100 transition-colors"><X size={16} /></button>
          </div>
          <div className="flex items-center gap-3.5">
            <div className={`w-12 h-12 rounded-2xl flex items-center justify-center text-sm font-bold shrink-0 ${avatarTint(user.email)}`}>
              {initials(user.name, user.email)}
            </div>
            <div className="min-w-0">
              <h3 className="text-base font-bold text-slate-900 truncate">{user.name}</h3>
              <p className="text-xs text-slate-500 truncate">{user.job_title || meta.label}</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 mt-4">
            <span className={`text-[10px] px-2 py-1 rounded-md font-bold inline-flex items-center gap-1 ${meta.badge || "bg-slate-200 text-slate-700"}`}>
              <Shield size={9} /> {meta.label || user.role}
            </span>
            {orgLabel && (
              <span className="badge badge-medium inline-flex items-center gap-1">
                <MapPin size={9} /> {orgLabel}
              </span>
            )}
          </div>
          <div className="flex gap-2 mt-4">
            <a href={mailtoUrl(user.email, "Cyber Assessment Agent — follow-up")} className="btn-primary flex-1 justify-center">
              <Mail size={13} /> Email
            </a>
            <button onClick={() => onEdit(user)} className="btn-secondary text-xs flex-1 justify-center flex items-center gap-1.5">
              <Pencil size={13} /> Edit
            </button>
          </div>
        </div>

        {/* Workload */}
        <div className="px-5 pt-5 shrink-0">
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-3">Workload</p>
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-2xl border border-slate-100 p-3.5" style={{ boxShadow: "var(--shadow-card)" }}>
              <div className="flex items-center gap-1.5 mb-1.5">
                <ClipboardCheck size={12} className="text-blue-600" />
                <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Assessments</p>
              </div>
              <p className="text-xl font-bold text-slate-900 tabular-nums">{w.assessments ?? 0}</p>
              <p className="text-[10px] text-slate-400 mt-0.5">{w.assessmentsOpen ?? 0} open · {w.assessmentsDone ?? 0} completed</p>
            </div>
            <div className="rounded-2xl border border-slate-100 p-3.5" style={{ boxShadow: "var(--shadow-card)" }}>
              <div className="flex items-center gap-1.5 mb-1.5">
                <FileText size={12} className="text-violet-600" />
                <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">{isContributor ? "Doc requests" : "Requests raised"}</p>
              </div>
              <p className="text-xl font-bold text-slate-900 tabular-nums">{isContributor ? (w.docRequests ?? 0) : (w.requestsRaised ?? 0)}</p>
              <p className="text-[10px] text-slate-400 mt-0.5">
                {isContributor ? `${w.docRequestsPending ?? 0} awaiting them` : "raised by this person"}
              </p>
            </div>
          </div>

          {isContributor && (w.docRequestList?.length > 0) && (
            <div className="mt-3 rounded-2xl border border-slate-100 overflow-hidden" style={{ boxShadow: "var(--shadow-card)" }}>
              <p className="px-4 py-2.5 text-[10px] font-bold text-slate-400 uppercase tracking-wider border-b border-slate-100 bg-slate-50/60">
                Requests for {orgLabel}
              </p>
              <div className="divide-y divide-slate-50 max-h-44 overflow-y-auto">
                {w.docRequestList.slice(0, 8).map((d) => (
                  <div key={d.id} className="px-4 py-2.5 flex items-center gap-2">
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-medium text-slate-800 truncate">{d.note || d.evidence_type || "Document"}</p>
                      <p className="text-[10px] text-slate-400 truncate">{d.assessment_name}</p>
                    </div>
                    <span className={`badge shrink-0 ${d.status === "accepted" ? "badge-success" : d.status === "rejected" ? "badge-critical" : "badge-medium"}`}>
                      {d.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {!isContributor && (w.assignedList?.length > 0) && (
            <div className="mt-3 rounded-2xl border border-slate-100 overflow-hidden" style={{ boxShadow: "var(--shadow-card)" }}>
              <p className="px-4 py-2.5 text-[10px] font-bold text-slate-400 uppercase tracking-wider border-b border-slate-100 bg-slate-50/60">
                Assigned assessments
              </p>
              <div className="divide-y divide-slate-50 max-h-44 overflow-y-auto">
                {w.assignedList.slice(0, 8).map((a) => (
                  <div key={a.id} className="px-4 py-2.5 flex items-center gap-2">
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-medium text-slate-800 truncate">{a.name}</p>
                      <p className="text-[10px] text-slate-400 truncate">{a.organization || "—"}</p>
                    </div>
                    <span className={`badge shrink-0 ${a.status === "completed" ? "badge-success" : a.status === "draft" ? "badge-neutral" : "badge-medium"}`}>
                      {String(a.status || "").replace("_", " ")}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Profile */}
        <div className="px-5 pt-5 pb-5 flex-1">
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-3">Profile</p>
          <div className="rounded-2xl border border-slate-100 divide-y divide-slate-50" style={{ boxShadow: "var(--shadow-card)" }}>
            <Field icon={Mail} label="Email">
              <div className="flex items-center gap-1 mt-0.5">
                <a href={mailtoUrl(user.email)} className="text-xs text-blue-600 hover:underline truncate">{user.email}</a>
                <CopyBtn value={user.email} title="Copy email" />
              </div>
            </Field>
            <Field icon={Phone} label="Phone">
              {user.phone ? (
                <div className="flex items-center gap-1 mt-0.5">
                  <a href={`tel:${user.phone}`} className="text-xs text-blue-600 hover:underline">{user.phone}</a>
                  <CopyBtn value={user.phone} title="Copy phone" />
                </div>
              ) : <p className="text-xs text-slate-300 mt-0.5">Not set</p>}
            </Field>
            <Field icon={Building2} label="Department" value={user.department} />
            <Field icon={BadgeCheck} label="Job title" value={user.job_title} />
            <Field icon={MapPin} label="Organization" value={orgLabel} />
            <Field icon={FileText} label="Notes" value={user.notes} />
            <Field icon={Clock} label="Added">
              <p className="text-xs text-slate-700 mt-0.5">
                {user.created_at ? new Date(user.created_at).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "—"}
              </p>
            </Field>
          </div>

          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mt-5 mb-2.5">Manage</p>
          <div className="flex gap-2">
            <button onClick={() => onToggleActive(user)}
              className="flex-1 flex items-center justify-center gap-1.5 text-xs font-semibold px-3 py-2.5 rounded-xl border border-slate-200 text-slate-700 hover:bg-slate-50 transition-colors">
              <Power size={13} /> {active ? "Deactivate" : "Reactivate"}
            </button>
            <button onClick={() => onDelete(user)}
              className="flex-1 flex items-center justify-center gap-1.5 text-xs font-semibold px-3 py-2.5 rounded-xl border border-rose-200 text-rose-600 hover:bg-rose-50 transition-colors">
              <Trash2 size={13} /> Delete
            </button>
          </div>
        </div>
      </div>
    </div>
    </Portal>
  );
}
