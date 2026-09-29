"use client";

import { useState } from "react";
import { ChevronRight, ChevronDown, Layers, Plus, X } from "lucide-react";

const CRIT_BADGE = { high: "badge-critical", medium: "badge-medium", low: "badge-low" };

function ControlRow({ ctrl, selected, onSelect }) {
  return (
    <button
      onClick={() => onSelect(ctrl.id)}
      className={`w-full flex items-center gap-2 pl-10 pr-3 py-2 text-left transition-colors ${
        selected ? "bg-blue-50 border-l-2 border-l-blue-500" : "hover:bg-slate-50 border-l-2 border-l-transparent"
      }`}
    >
      <span className="badge badge-low font-mono text-[9px] shrink-0">{ctrl.code}</span>
      <span className={`text-xs flex-1 min-w-0 truncate ${selected ? "font-bold text-blue-800" : "font-medium text-slate-700"}`}>
        {ctrl.name || ctrl.statement || "—"}
      </span>
      {ctrl.criticality && (
        <span className={`badge ${CRIT_BADGE[ctrl.criticality] || "badge-neutral"} text-[9px] shrink-0`}>{ctrl.criticality}</span>
      )}
      <span className="text-[10px] text-slate-400 shrink-0">{(ctrl.questions || []).length}q</span>
    </button>
  );
}

function CategorySection({ cat, selectedPk, onSelect, onAddControl, canEdit, canAddControls }) {
  const [open, setOpen] = useState(true);
  const controls = cat.controls || [];
  const hasRealName = cat.name && cat.name.trim();

  return (
    <div className="border-b border-slate-50 last:border-0">
      {hasRealName && (
        <div className={`flex items-center gap-2 pl-6 pr-2 py-1.5 group ${open ? "bg-indigo-50/30" : ""}`}>
          <button onClick={() => setOpen(v => !v)} className="flex items-center gap-2 flex-1 min-w-0 text-left hover:opacity-80">
            {open ? <ChevronDown size={11} className="text-indigo-400 shrink-0" /> : <ChevronRight size={11} className="text-slate-300 shrink-0" />}
            <Layers size={10} className="text-indigo-300 shrink-0" />
            <span className="text-[11px] font-semibold text-slate-700 flex-1 truncate">{cat.name}</span>
            <span className="text-[10px] text-slate-400 shrink-0">{controls.length}c</span>
          </button>
          {canAddControls && (
            <button
              onClick={() => onAddControl(cat.id)}
              title="Add control to this sub-domain"
              className="p-1 rounded text-slate-300 hover:text-blue-600 hover:bg-blue-50 opacity-0 group-hover:opacity-100 transition-all"
            >
              <Plus size={12} />
            </button>
          )}
        </div>
      )}
      {open && controls.map(c => (
        <ControlRow key={c.id} ctrl={c} selected={c.id === selectedPk} onSelect={onSelect} />
      ))}
      {open && controls.length === 0 && (
        <p className="pl-10 pr-3 py-2 text-[11px] text-slate-300 italic">No controls</p>
      )}
    </div>
  );
}

function DomainSection({ domain, selectedPk, onSelect, onAddControl, canEdit, canAddControls, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen || false);
  const categories = domain.categories || [];
  const cCount = (domain.controls || []).length;

  return (
    <div className="border-b border-slate-100 last:border-0">
      <button
        onClick={() => setOpen(v => !v)}
        className={`w-full flex items-center gap-2.5 px-3 py-2.5 text-left transition-colors ${open ? "bg-slate-50" : "hover:bg-slate-50/60"}`}
      >
        {open ? <ChevronDown size={12} className="text-blue-600 shrink-0" /> : <ChevronRight size={12} className="text-slate-400 shrink-0" />}
        <span className="badge badge-low font-mono shrink-0">{domain.code}</span>
        <span className="text-[13px] font-bold text-slate-900 flex-1 truncate">{domain.name}</span>
        <span className="text-[10px] text-slate-400 shrink-0">{cCount} ctrl</span>
      </button>
      {open && (
        <div>
          {categories.map(cat => (
            <CategorySection
              key={cat.id} cat={cat} selectedPk={selectedPk}
              onSelect={onSelect} onAddControl={onAddControl} canEdit={canEdit} canAddControls={canAddControls}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function CatalogTree({ framework, selectedPk, onSelect, onAddControl, canEdit, canAddControls }) {
  const [search, setSearch] = useState("");
  if (!framework) return <p className="p-6 text-sm text-slate-400 text-center">Select a framework to browse its controls.</p>;

  const domains = framework.domains || [];
  const s = search.trim().toLowerCase();
  const filtered = s
    ? domains
        .map(d => ({
          ...d,
          categories: (d.categories || [])
            .map(cat => ({
              ...cat,
              controls: (cat.controls || []).filter(c =>
                c.code?.toLowerCase().includes(s) ||
                c.name?.toLowerCase().includes(s) ||
                c.statement?.toLowerCase().includes(s)
              ),
            }))
            .filter(cat => cat.controls.length > 0),
        }))
        .filter(d => (d.categories?.length || 0) > 0)
    : domains;

  return (
    <div className="flex flex-col h-full">
      <div className="shrink-0 px-3 py-2.5 border-b border-slate-100 flex items-center gap-2">
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Filter this framework…"
          className="flex-1 text-xs text-slate-700 bg-slate-50 border border-slate-200 rounded-lg px-3 py-1.5 outline-none focus:border-blue-400 focus:bg-white transition-all placeholder:text-slate-400"
        />
        {search && <button onClick={() => setSearch("")} className="text-slate-400 hover:text-slate-600"><X size={13} /></button>}
      </div>
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 ? (
          <p className="text-sm text-slate-400 text-center py-12">No controls match "{search}".</p>
        ) : (
          filtered.map((d, i) => (
            <DomainSection
              key={d.id} domain={d} selectedPk={selectedPk}
              onSelect={onSelect} onAddControl={onAddControl} canEdit={canEdit} canAddControls={canAddControls}
              defaultOpen={i === 0 || !!s}
            />
          ))
        )}
      </div>
    </div>
  );
}
