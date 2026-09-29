"use client";

import { useEffect, useState, useCallback } from "react";
import { Save, RotateCcw, Copy, Trash2, Plus, X, History, Pencil, AlertTriangle, Lock } from "lucide-react";
import { api } from "@/lib/api";
import { canEdit, canDelete } from "@/lib/auth";

const QUESTION_TYPES = ["YES_NO", "MULTI_CHOICE", "SCALE_1_5", "MATURITY_SCALE", "NUMERIC", "TEXT"];
const CRITICALITIES = ["low", "medium", "high"];

function emptyQuestion(order) {
  return { id: null, text: "", question_type: "YES_NO", weight: 1, help_text: "", order_index: order };
}

function EditorTab({ pk, role, frameworkEditable = true, questionEditOnly = false, onChanged, onDeleted, onDuplicated }) {
  const [detail,  setDetail]  = useState(null);
  const [form,    setForm]    = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving,  setSaving]  = useState(false);
  const [busy,    setBusy]    = useState(false);
  const [msg,     setMsg]     = useState(null);
  const [conflict, setConflict] = useState(false);

  const writable  = canEdit(role, "framework") && (frameworkEditable || questionEditOnly);
  const policyEditable = canEdit(role, "framework") && frameworkEditable && !questionEditOnly;
  const deletable = canDelete(role, "framework") && frameworkEditable && !questionEditOnly;

  const load = useCallback(async () => {
    setLoading(true); setMsg(null); setConflict(false);
    try {
      const d = await api.catalog.control.get(pk);
      setDetail(d);
      setForm({
        name: d.name || "",
        statement: d.statement || "",
        weight: d.weight,
        criticality: d.criticality,
        questions: (d.questions || []).map(q => ({
          id: q.id, text: q.text || "", question_type: q.question_type || "YES_NO",
          weight: q.weight ?? 1, help_text: q.help_text || "", order_index: q.order_index ?? 0,
        })),
      });
    } catch (e) { setMsg({ ok: false, text: e.message }); }
    finally { setLoading(false); }
  }, [pk]);

  useEffect(() => { load(); }, [load]);

  if (loading || !form) return <div className="p-5 space-y-3">{[1,2,3].map(i => <div key={i} className="skeleton h-10 rounded-xl" />)}</div>;
  if (!detail) return <p className="p-6 text-sm text-rose-500">{msg?.text || "Could not load control."}</p>;

  const detailSnapshot = questionEditOnly ? {
    questions: (detail.questions || []).map(q => ({ id: q.id, text: q.text || "", question_type: q.question_type || "YES_NO", weight: q.weight ?? 1, help_text: q.help_text || "", order_index: q.order_index ?? 0 })),
  } : {
    name: detail.name || "", statement: detail.statement || "", weight: detail.weight,
    criticality: detail.criticality,
    questions: (detail.questions || []).map(q => ({ id: q.id, text: q.text || "", question_type: q.question_type || "YES_NO", weight: q.weight ?? 1, help_text: q.help_text || "", order_index: q.order_index ?? 0 })),
  };
  const formSnapshot = questionEditOnly ? {
    questions: form.questions,
  } : form;
  const dirty = JSON.stringify(detailSnapshot) !== JSON.stringify(formSnapshot);

  const setQ = (i, patch) => setForm(f => ({ ...f, questions: f.questions.map((q, idx) => idx === i ? { ...q, ...patch } : q) }));
  const addQ = () => setForm(f => ({ ...f, questions: [...f.questions, emptyQuestion(f.questions.length)] }));
  const removeQ = (i) => setForm(f => ({ ...f, questions: f.questions.filter((_, idx) => idx !== i) }));

  const save = async () => {
    setSaving(true); setMsg(null); setConflict(false);
    try {
      const payload = {
        questions: form.questions.map((q, i) => ({
          id: q.id, text: q.text, question_type: q.question_type,
          weight: Number(q.weight) || 1, help_text: q.help_text || null, order_index: i,
        })),
        row_hash: detail.row_hash,
      };
      if (policyEditable) {
        payload.name = form.name;
        payload.statement = form.statement;
        payload.weight = Number(form.weight);
        payload.criticality = form.criticality;
      }
      const updated = await api.catalog.control.update(pk, payload);
      setDetail(updated);
      setMsg({ ok: true, text: "Saved." });
      onChanged?.();
    } catch (e) {
      if (String(e.message).includes("409")) { setConflict(true); setMsg({ ok: false, text: "This control changed since you opened it." }); }
      else setMsg({ ok: false, text: e.message });
    } finally { setSaving(false); }
  };

  const duplicate = async () => {
    setBusy(true); setMsg(null);
    try { const dup = await api.catalog.control.duplicate(pk); onDuplicated?.(dup); }
    catch (e) { setMsg({ ok: false, text: e.message }); } finally { setBusy(false); }
  };

  const remove = async () => {
    if (!confirm(`Delete control "${detail.code}"? This cannot be undone.`)) return;
    setBusy(true); setMsg(null);
    try { await api.catalog.control.delete(pk); onDeleted?.(pk); }
    catch (e) { setMsg({ ok: false, text: e.message }); setBusy(false); }
  };

  const inputCls = "w-full text-sm text-slate-800 border border-slate-200 rounded-xl px-3.5 py-2.5 outline-none focus:border-blue-400 transition-all disabled:bg-slate-50 disabled:text-slate-400";

  return (
    <div className="p-5 space-y-4">
      {/* header row */}
      <div className="flex items-center gap-2">
        <span className="badge badge-low font-mono">{detail.code}</span>
        <span className="text-[11px] text-slate-400 font-mono">{detail.framework_code}</span>
        <span className="text-slate-300">·</span>
        <span className="text-[11px] text-slate-500 truncate">{detail.domain_code} / {detail.category_code || "—"}</span>
        {!frameworkEditable && (
          <span className="ml-auto flex items-center gap-1 text-[10px] font-semibold text-slate-400 bg-slate-100 border border-slate-200 rounded-lg px-2 py-0.5">
            <Lock size={9} /> Read-only
          </span>
        )}
      </div>

      {msg && (
        <div className={`flex items-center gap-2 text-xs px-3 py-2 rounded-lg border ${msg.ok ? "bg-emerald-50 border-emerald-200 text-emerald-700" : "bg-rose-50 border-rose-200 text-rose-700"}`}>
          {!msg.ok && <AlertTriangle size={13} />}{msg.text}
          {conflict && <button onClick={load} className="ml-auto font-semibold underline">Reload latest</button>}
        </div>
      )}

      <div>
        <label className="block text-[11px] font-semibold text-slate-500 mb-1">Name</label>
        <input className={inputCls} value={form.name} disabled={!policyEditable}
          onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
      </div>

      <div>
        <label className="block text-[11px] font-semibold text-slate-500 mb-1">Statement</label>
        <textarea rows={4} className={inputCls + " resize-y leading-relaxed"} value={form.statement} disabled={!policyEditable}
          onChange={e => setForm(f => ({ ...f, statement: e.target.value }))} />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-[11px] font-semibold text-slate-500 mb-1">Weight</label>
          <input type="number" step="0.5" min="0" className={inputCls} value={form.weight} disabled={!policyEditable}
            onChange={e => setForm(f => ({ ...f, weight: e.target.value }))} />
        </div>
        <div>
          <label className="block text-[11px] font-semibold text-slate-500 mb-1">Criticality</label>
          <select className={inputCls} value={form.criticality} disabled={!policyEditable}
            onChange={e => setForm(f => ({ ...f, criticality: e.target.value }))}>
            {CRITICALITIES.map(cc => <option key={cc} value={cc}>{cc}</option>)}
          </select>
        </div>
      </div>

      {/* Questions sub-editor */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <label className="text-[11px] font-semibold text-slate-500">Questions ({form.questions.length})</label>
          {writable && (
            <button onClick={addQ} className="flex items-center gap-1 text-xs font-semibold text-blue-600 hover:text-blue-700">
              <Plus size={12} /> Add question
            </button>
          )}
        </div>
        <div className="space-y-2">
          {form.questions.length === 0 && <p className="text-[11px] text-slate-400 italic">No questions — this control will be flagged in validation.</p>}
          {form.questions.map((q, i) => (
            <div key={q.id || `new-${i}`} className="border border-slate-200 rounded-xl p-3 bg-slate-50/50">
              <div className="flex items-start gap-2">
                <textarea rows={2} className="flex-1 text-xs text-slate-700 border border-slate-200 rounded-lg px-2.5 py-2 outline-none focus:border-blue-400 resize-y bg-white disabled:bg-slate-50"
                  value={q.text} disabled={!writable} placeholder="Question text…"
                  onChange={e => setQ(i, { text: e.target.value })} />
                {writable && (
                  <button onClick={() => removeQ(i)} className="p-1 rounded text-slate-300 hover:text-rose-500 hover:bg-rose-50 shrink-0">
                    <X size={13} />
                  </button>
                )}
              </div>
              <div className="flex items-center gap-2 mt-2">
                <select className="text-[11px] border border-slate-200 rounded-lg px-2 py-1 bg-white outline-none focus:border-blue-400 disabled:bg-slate-50"
                  value={q.question_type} disabled={!writable} onChange={e => setQ(i, { question_type: e.target.value })}>
                  {QUESTION_TYPES.map(t => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
                </select>
                <label className="text-[10px] text-slate-400">weight</label>
                <input type="number" step="0.5" min="0" className="w-16 text-[11px] border border-slate-200 rounded-lg px-2 py-1 bg-white outline-none focus:border-blue-400 disabled:bg-slate-50"
                  value={q.weight} disabled={!writable} onChange={e => setQ(i, { weight: e.target.value })} />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Actions */}
      {writable && (
        <div className="flex items-center gap-2 pt-2 border-t border-slate-100">
          <button onClick={save} disabled={!dirty || saving} className="btn-primary">
            <Save size={13} /> {saving ? "Saving…" : "Save"}
          </button>
          <button onClick={load} disabled={!dirty || saving} className="btn-secondary">
            <RotateCcw size={13} /> Revert
          </button>
          {policyEditable && (
            <>
              <button onClick={duplicate} disabled={busy} className="btn-secondary ml-auto">
                <Copy size={13} /> Duplicate
              </button>
              {deletable && (
                <button onClick={remove} disabled={busy}
                  className="p-2 rounded-lg text-slate-400 hover:text-rose-600 hover:bg-rose-50 transition-all border border-slate-200">
                  <Trash2 size={14} />
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function HistoryTab({ pk }) {
  const [rows, setRows] = useState(null);
  useEffect(() => { api.catalog.control.history(pk).then(setRows).catch(() => setRows([])); }, [pk]);
  if (rows === null) return <div className="p-5 space-y-2">{[1,2].map(i => <div key={i} className="skeleton h-12 rounded-xl" />)}</div>;
  if (rows.length === 0) return <p className="p-6 text-sm text-slate-400 text-center">No recorded changes yet.</p>;
  return (
    <div className="p-5 space-y-2">
      {rows.map(r => (
        <div key={r.id} className="border border-slate-100 rounded-xl p-3 bg-white" style={{ boxShadow: "var(--shadow-card)" }}>
          <div className="flex items-center gap-2 mb-1">
            <span className="badge badge-neutral capitalize">{r.action}</span>
            <span className="text-[11px] text-slate-400">{r.user_email || "system"}</span>
            <span className="text-[10px] text-slate-300 ml-auto">{r.created_at ? new Date(r.created_at).toLocaleString() : ""}</span>
          </div>
          {r.changes && (r.changes.before || r.changes.after) && (
            <pre className="text-[10px] text-slate-500 bg-slate-50 rounded-lg p-2 overflow-x-auto max-h-40">
              {JSON.stringify(r.changes, null, 2)}
            </pre>
          )}
        </div>
      ))}
    </div>
  );
}

export default function ControlEditor({ pk, role, frameworkEditable, questionEditOnly, onChanged, onDeleted, onDuplicated }) {
  const [tab, setTab] = useState("editor");
  useEffect(() => { setTab("editor"); }, [pk]);

  return (
    <div className="flex flex-col h-full">
      <div className="shrink-0 flex items-center gap-1 px-4 border-b border-slate-200 bg-white">
        {[["editor", "Editor", Pencil], ["history", "History", History]].map(([key, label, Icon]) => {
          const active = tab === key;
          return (
            <button key={key} onClick={() => setTab(key)}
              className={`flex items-center gap-1.5 px-3 py-3 text-[13px] font-medium border-b-2 transition-colors ${
                active ? "border-blue-600 text-blue-700" : "border-transparent text-slate-500 hover:text-slate-700"
              }`}>
              <Icon size={13} strokeWidth={active ? 2.5 : 1.8} /> {label}
            </button>
          );
        })}
      </div>
      <div className="flex-1 overflow-y-auto">
        {tab === "editor"
          ? <EditorTab pk={pk} role={role} frameworkEditable={frameworkEditable} questionEditOnly={questionEditOnly} onChanged={onChanged} onDeleted={onDeleted} onDuplicated={onDuplicated} />
          : <HistoryTab pk={pk} />}
      </div>
    </div>
  );
}
