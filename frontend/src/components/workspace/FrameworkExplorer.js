"use client";

import { useState } from "react";
import { ChevronRight, ChevronDown, HelpCircle, Shield, Layers, X } from "lucide-react";

const TYPE_BADGE = { YES_NO: "badge-success", MULTI_CHOICE: "badge-medium", SCALE_1_5: "badge-low", TEXT: "badge-neutral", PRE_ASSESSMENT: "badge-medium" };
const CRIT_BADGE = { high: "badge-critical", medium: "badge-medium", low: "badge-low" };

function QuestionItem({ q }) {
  return (
    <div className="flex items-start gap-3 pl-16 pr-4 py-2.5 bg-blue-50/30 border-b border-blue-50/50 last:border-0 hover:bg-blue-50/50 transition-colors">
      <HelpCircle size={11} className="text-blue-400 mt-0.5 shrink-0" />
      <p className="text-xs text-slate-600 flex-1 leading-relaxed">{q.text}</p>
      <span className={`badge ${TYPE_BADGE[q.question_type] || "badge-neutral"} text-[9px] shrink-0`}>
        {(q.question_type || "").replace(/_/g, " ")}
      </span>
    </div>
  );
}

function ControlItem({ ctrl, indent = "pl-10" }) {
  const [open, setOpen] = useState(false);
  const qCount = (ctrl.questions || []).length;
  return (
    <div>
      <button onClick={() => setOpen(v => !v)}
        className={`w-full flex items-center gap-2 ${indent} pr-4 py-2.5 hover:bg-slate-50 transition-colors text-left ${open ? "bg-slate-50/80" : ""}`}>
        {open ? <ChevronDown size={11} className="text-blue-500 shrink-0" /> : <ChevronRight size={11} className="text-slate-300 shrink-0" />}
        <span className="badge badge-low font-mono text-[9px] shrink-0">{ctrl.code}</span>
        <span className="text-xs font-semibold text-slate-700 flex-1 min-w-0 truncate">{ctrl.name}</span>
        {ctrl.criticality && <span className={`badge ${CRIT_BADGE[ctrl.criticality] || "badge-neutral"} text-[9px] shrink-0`}>{ctrl.criticality}</span>}
        <span className="text-[10px] text-slate-400 shrink-0 ml-1">{qCount}q</span>
      </button>
      {open && ctrl.statement && (
        <div className="pl-16 pr-4 py-2 bg-slate-50/60 border-b border-slate-100 border-l-2 border-l-blue-200">
          <p className="text-[11px] text-slate-500 leading-relaxed italic">{ctrl.statement}</p>
        </div>
      )}
      {open && (ctrl.questions || []).map(q => <QuestionItem key={q.id} q={q} />)}
    </div>
  );
}

function CategorySection({ cat }) {
  const [open, setOpen] = useState(false);
  const controls = cat.controls || [];
  const qCount = controls.reduce((s, c) => s + (c.questions?.length || 0), 0);
  // Skip rendering the category row when it is a synthetic passthrough (no real name)
  const hasRealName = cat.name && cat.name.trim();
  if (!hasRealName) {
    return (
      <div className="divide-y divide-slate-50">
        {controls.map(ctrl => <ControlItem key={ctrl.id} ctrl={ctrl} indent="pl-10" />)}
      </div>
    );
  }
  return (
    <div className="border-b border-slate-50 last:border-0">
      <button onClick={() => setOpen(v => !v)}
        className={`w-full flex items-center gap-2 pl-7 pr-4 py-2 hover:bg-indigo-50/40 transition-colors text-left ${open ? "bg-indigo-50/30" : ""}`}>
        {open ? <ChevronDown size={11} className="text-indigo-400 shrink-0" /> : <ChevronRight size={11} className="text-slate-300 shrink-0" />}
        <Layers size={10} className="text-indigo-300 shrink-0" />
        {cat.code && <span className="font-mono text-[9px] text-slate-400 shrink-0">{cat.code}</span>}
        <span className="text-[11px] font-semibold text-slate-700 flex-1 truncate">{cat.name}</span>
        <span className="text-[10px] text-slate-400 shrink-0">{controls.length}c · {qCount}q</span>
      </button>
      {open && (
        <div className="divide-y divide-slate-50 pl-3">
          {controls.map(ctrl => <ControlItem key={ctrl.id} ctrl={ctrl} indent="pl-10" />)}
        </div>
      )}
    </div>
  );
}

function DomainSection({ domain, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen || false);
  const categories = domain.categories || [];
  const hasCategories = categories.length > 0;
  // Use flat controls for counts (always populated as back-compat)
  const cCount = (domain.controls || []).length;
  const qCount = (domain.controls || []).reduce((s, c) => s + (c.questions?.length || 0), 0);
  return (
    <div className="border-b border-slate-100 last:border-0">
      <button onClick={() => setOpen(v => !v)}
        className={`w-full flex items-center gap-3 px-4 py-3.5 text-left transition-colors ${open ? "bg-slate-50" : "hover:bg-slate-50/60"}`}>
        {open ? <ChevronDown size={13} className="text-blue-600 shrink-0" /> : <ChevronRight size={13} className="text-slate-400 shrink-0" />}
        <span className="badge badge-low font-mono shrink-0">{domain.code}</span>
        <span className="text-sm font-bold text-slate-900 flex-1 truncate">{domain.name}</span>
        <span className="text-[10px] text-slate-400 shrink-0">{cCount} ctrl · {qCount}q</span>
      </button>
      {open && (
        <div className="divide-y divide-slate-50">
          {hasCategories
            ? categories.map(cat => <CategorySection key={cat.id} cat={cat} />)
            : (domain.controls || []).map(ctrl => <ControlItem key={ctrl.id} ctrl={ctrl} />)
          }
        </div>
      )}
    </div>
  );
}

export default function FrameworkExplorer({ framework }) {
  const [search, setSearch] = useState("");
  if (!framework) return null;

  const domains = framework.domains || [];
  const totalControls  = domains.reduce((s, d) => s + (d.controls?.length || 0), 0);
  const totalQuestions = domains.reduce((s, d) => d.controls?.reduce((s2, c) => s2 + (c.questions?.length || 0), 0) + s, 0);

  const s = search.toLowerCase();
  const filtered = search.trim() ? domains.map(d => {
    const cats = (d.categories || []).map(cat => ({
      ...cat,
      controls: (cat.controls || []).filter(c =>
        c.code?.toLowerCase().includes(s) ||
        c.name?.toLowerCase().includes(s) ||
        c.statement?.toLowerCase().includes(s)
      ),
    })).filter(cat => cat.controls.length > 0);
    return { ...d, categories: cats, controls: (d.controls || []).filter(c =>
      c.code?.toLowerCase().includes(s) ||
      c.name?.toLowerCase().includes(s) ||
      c.statement?.toLowerCase().includes(s)
    ) };
  }).filter(d => (d.categories?.length || 0) > 0 || (d.controls?.length || 0) > 0) : domains;

  return (
    <div>
      <div className="flex items-center gap-4 px-4 py-3.5 bg-linear-to-r from-slate-50 to-blue-50/30 border-b border-slate-100">
        <Shield size={15} className="text-blue-700 shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-bold text-slate-900">{framework.name}</p>
          <p className="text-[10px] text-slate-400">{framework.code} · v{framework.version}</p>
        </div>
        <span className="badge badge-neutral">{totalControls} controls</span>
        <span className="badge badge-neutral">{totalQuestions} questions</span>
      </div>
      <div className="px-4 py-2.5 border-b border-slate-100 flex items-center gap-2">
        <input value={search} onChange={e => setSearch(e.target.value)}
          placeholder="Search controls by code or name…"
          className="flex-1 text-xs text-slate-700 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 outline-none focus:border-blue-400 focus:bg-white transition-all placeholder:text-slate-400" />
        {search && <button onClick={() => setSearch("")} className="text-slate-400 hover:text-slate-600"><X size={13} /></button>}
      </div>
      <div>
        {filtered.length === 0 ? (
          <p className="text-sm text-slate-400 text-center py-12">No controls match "{search}"</p>
        ) : (
          filtered.map((d, i) => <DomainSection key={d.id} domain={d} defaultOpen={i === 0 && !search} />)
        )}
      </div>
    </div>
  );
}
