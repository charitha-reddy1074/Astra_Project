"use client";

import { useEffect, useState, useCallback } from "react";
import { FileText, ChevronDown, ChevronUp, HelpCircle } from "lucide-react";
import { api } from "@/lib/api";

const Q_TYPE_LABEL = {
  YES_NO: "Yes / No", FREE_TEXT: "Free Text", SCALE_1_5: "Scale 1–5",
  MULTI_CHOICE: "Multiple Choice", INTERVIEW: "Interview", PRE_ASSESSMENT: "Pre-Assessment",
};

function ControlRow({ control }) {
  const [open, setOpen] = useState(false);
  const qs = control.questions || [];
  return (
    <div className="border border-slate-100 rounded-xl overflow-hidden hover:border-blue-100 transition-colors">
      <button onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 bg-white hover:bg-slate-50/60 transition-colors text-left">
        <span className="badge badge-low font-mono shrink-0">{control.code}</span>
        <span className="text-sm font-medium text-slate-800 flex-1 truncate">{control.name}</span>
        <span className="text-[10px] text-slate-400 shrink-0 mr-2">{qs.length}q</span>
        {open ? <ChevronUp size={13} className="text-slate-400 shrink-0" /> : <ChevronDown size={13} className="text-slate-400 shrink-0" />}
      </button>
      {open && qs.length > 0 && (
        <div className="bg-slate-50/60 border-t border-slate-100 divide-y divide-slate-100">
          {qs.map((q, i) => (
            <div key={q.id || i} className="px-4 py-3 flex items-start gap-3">
              <HelpCircle size={13} className="text-blue-300 mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-slate-700 leading-snug">{q.text}</p>
                <span className="badge badge-neutral mt-1.5">{Q_TYPE_LABEL[q.question_type] || q.question_type}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DomainSection({ domain }) {
  const [open, setOpen] = useState(false);
  const controls = domain.controls || [];
  const totalQ   = controls.reduce((n, c) => n + (c.questions?.length || 0), 0);
  return (
    <div className="border border-slate-200 rounded-2xl overflow-hidden">
      <button onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-3 px-5 py-4 bg-white hover:bg-slate-50/60 transition-colors text-left">
        <span className="badge badge-neutral font-mono shrink-0">{domain.code}</span>
        <span className="text-sm font-semibold text-slate-900 flex-1">{domain.name}</span>
        <span className="text-xs text-slate-400 shrink-0">{totalQ} questions</span>
        {open ? <ChevronUp size={14} className="text-slate-400 shrink-0" /> : <ChevronDown size={14} className="text-slate-400 shrink-0" />}
      </button>
      {open && (
        <div className="bg-slate-50/40 border-t border-slate-100 p-4 space-y-2">
          {controls.map((c) => <ControlRow key={c.id} control={c} />)}
        </div>
      )}
    </div>
  );
}

export default function QuestionnairesView() {
  const [frameworks,     setFrameworks]     = useState([]);
  const [selected,       setSelected]       = useState(null);
  const [detail,         setDetail]         = useState(null);
  const [loading,        setLoading]        = useState(true);
  const [detailLoading,  setDetailLoading]  = useState(false);

  useEffect(() => {
    api.frameworks.list()
      .then((list) => { setFrameworks(list); if (list.length > 0) setSelected(list[0]); })
      .catch(console.error).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!selected?.id) { setDetail(null); return; }
    setDetailLoading(true);
    api.frameworks.get(selected.id).then(setDetail).catch(console.error).finally(() => setDetailLoading(false));
  }, [selected?.id]);

  const totalQs = detail ? (detail.domains || []).flatMap((d) => d.controls || []).reduce((n, c) => n + (c.questions?.length || 0), 0) : 0;

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-5 fade-in-up">
      <div>
        <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Questionnaires</h2>
        <p className="text-sm text-slate-500 mt-1">Browse questions by framework, domain, and control.</p>
      </div>

      {loading ? (
        <div className="space-y-3">{[1,2,3].map((i) => <div key={i} className="skeleton h-16 rounded-2xl" />)}</div>
      ) : frameworks.length === 0 ? (
        <div className="bg-white border border-slate-100 rounded-2xl py-20 text-center" style={{ boxShadow: "var(--shadow-card)" }}>
          <FileText size={28} className="text-slate-200 mx-auto mb-4" />
          <p className="text-sm font-semibold text-slate-500 mb-1">No frameworks loaded</p>
          <p className="text-xs text-slate-400">Load frameworks to browse their questionnaires.</p>
        </div>
      ) : (
        <>
          <div className="flex gap-1.5 bg-white border border-slate-200 rounded-xl p-1 overflow-x-auto">
            {frameworks.map((fw) => (
              <button key={fw.id} onClick={() => setSelected(fw)}
                className={`px-4 py-2 rounded-lg text-xs font-semibold whitespace-nowrap transition-colors shrink-0 ${
                  selected?.id === fw.id ? "bg-blue-700 text-white shadow-sm" : "text-slate-500 hover:bg-slate-50"}`}>
                {fw.name}
              </button>
            ))}
          </div>

          {detailLoading ? (
            <div className="space-y-3">{[1,2,3].map((i) => <div key={i} className="skeleton h-16 rounded-2xl" />)}</div>
          ) : detail ? (
            <>
              <div className="flex items-center gap-4">
                <span className="badge badge-neutral">{(detail.domains || []).length} domains</span>
                <span className="badge badge-neutral">{detail.total_controls} controls</span>
                <span className="badge badge-neutral">{totalQs} questions</span>
              </div>
              <div className="space-y-3">
                {(detail.domains || []).map((d) => <DomainSection key={d.id} domain={d} />)}
              </div>
            </>
          ) : null}
        </>
      )}
    </div>
  );
}
