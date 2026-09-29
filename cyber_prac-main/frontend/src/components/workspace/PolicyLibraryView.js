"use client";

import { useEffect, useState, useMemo } from "react";
import { Library, Search, ChevronDown, ChevronUp, X, BookOpen, Shield } from "lucide-react";
import { api } from "@/lib/api";

function PolicyCard({ control, domain }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="bg-white border border-slate-100 rounded-xl overflow-hidden hover:border-blue-100 hover:shadow-sm transition-all">
      <button onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-4 text-left hover:bg-slate-50/60 transition-colors">
        <div className="w-8 h-8 bg-blue-50 rounded-lg flex items-center justify-center shrink-0">
          <Shield size={13} className="text-blue-600" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="badge badge-low font-mono text-[10px]">{control.code}</span>
            <span className="badge badge-neutral text-[10px]">{domain.code}</span>
          </div>
          <p className="text-sm font-semibold text-slate-900 truncate">{control.name}</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-[10px] text-slate-400">{(control.questions || []).length}q</span>
          {open ? <ChevronUp size={13} className="text-slate-400" /> : <ChevronDown size={13} className="text-slate-400" />}
        </div>
      </button>
      {open && control.statement && (
        <div className="px-4 pb-4 pt-1 border-t border-slate-100 bg-slate-50/40">
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2">Policy Statement</p>
          <p className="text-sm text-slate-700 leading-relaxed">{control.statement}</p>
          <div className="flex flex-wrap gap-2 mt-3">
            <span className="badge badge-neutral">Domain: {domain.name}</span>
            {control.criticality && (
              <span className={`badge ${control.criticality === "high" ? "badge-critical" : control.criticality === "medium" ? "badge-medium" : "badge-neutral"}`}>
                {control.criticality} priority
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function PolicyLibraryView() {
  const [frameworks,    setFrameworks]    = useState([]);
  const [selected,      setSelected]      = useState(null);
  const [detail,        setDetail]        = useState(null);
  const [loading,       setLoading]       = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [search,        setSearch]        = useState("");

  useEffect(() => {
    api.frameworks.list().then((list) => { setFrameworks(list); if (list.length > 0) setSelected(list[0]); })
      .catch(console.error).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!selected?.id) { setDetail(null); return; }
    setDetailLoading(true);
    api.frameworks.get(selected.id).then(setDetail).catch(console.error).finally(() => setDetailLoading(false));
  }, [selected?.id]);

  const allControls = useMemo(() => {
    if (!detail) return [];
    const items = [];
    for (const domain of detail.domains || []) for (const control of domain.controls || []) items.push({ control, domain });
    return items;
  }, [detail]);

  const filtered = useMemo(() => {
    if (!search.trim()) return allControls;
    const q = search.toLowerCase();
    return allControls.filter(({ control, domain }) =>
      control.code?.toLowerCase().includes(q) || control.name?.toLowerCase().includes(q) ||
      control.statement?.toLowerCase().includes(q) || domain.name?.toLowerCase().includes(q));
  }, [allControls, search]);

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-5 fade-in-up">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Policy Library</h2>
          <p className="text-sm text-slate-500 mt-1">Active security policies and control documentation relevant to your assessments.</p>
        </div>
        {detail && (
          <div className="flex items-center gap-2 bg-white border border-slate-200 rounded-xl px-4 py-2.5 w-64 shrink-0 focus-within:border-blue-400 focus-within:ring-2 focus-within:ring-blue-50 transition-all">
            <Search size={14} className="text-slate-400 shrink-0" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search policies…"
              className="flex-1 text-sm text-slate-700 outline-none placeholder:text-slate-400 bg-transparent min-w-0" />
            {search && <button onClick={() => setSearch("")} className="text-slate-400 hover:text-slate-600"><X size={12} /></button>}
          </div>
        )}
      </div>

      {loading ? (
        <div className="space-y-3">{[1,2,3].map((i) => <div key={i} className="skeleton h-16 rounded-2xl" />)}</div>
      ) : frameworks.length === 0 ? (
        <div className="bg-white border border-slate-100 rounded-2xl py-20 text-center" style={{ boxShadow: "var(--shadow-card)" }}>
          <Library size={28} className="text-slate-200 mx-auto mb-4" />
          <p className="text-sm font-semibold text-slate-500 mb-1">No policies available</p>
          <p className="text-xs text-slate-400 max-w-xs mx-auto">
            Security policies are derived from loaded frameworks. Contact your assessor to load frameworks.
          </p>
        </div>
      ) : (
        <>
          {/* Framework tabs */}
          <div className="flex gap-1.5 bg-white border border-slate-200 rounded-xl p-1 overflow-x-auto">
            {frameworks.map((fw) => (
              <button key={fw.id} onClick={() => { setSelected(fw); setSearch(""); }}
                className={`px-4 py-2 rounded-lg text-xs font-semibold whitespace-nowrap transition-colors shrink-0 ${
                  selected?.id === fw.id ? "brand-tile text-white shadow-sm" : "text-slate-500 hover:bg-slate-50"}`}>
                {fw.name}
              </button>
            ))}
          </div>

          {/* Stats */}
          {detail && !detailLoading && (
            <div className="grid grid-cols-3 gap-4">
              {[
                { label: "Total Policies", value: allControls.length,        icon: BookOpen,  accent: "brand-tile"    },
                { label: "Domains",        value: (detail.domains || []).length, icon: Shield,   accent: "brand-tile"  },
                { label: "Matching",       value: search ? filtered.length : allControls.length, icon: Search, accent: "bg-emerald-700" },
              ].map(({ label, value, icon: Icon, accent }) => (
                <div key={label} className="bg-white border border-slate-100 rounded-2xl p-4 flex items-center gap-3" style={{ boxShadow: "var(--shadow-card)" }}>
                  <div className={`w-9 h-9 ${accent} rounded-xl flex items-center justify-center shrink-0`}>
                    <Icon size={14} className="text-white" />
                  </div>
                  <div>
                    <p className="text-lg font-bold text-slate-900 tabular-nums">{value}</p>
                    <p className="text-[10px] text-slate-400">{label}</p>
                  </div>
                </div>
              ))}
            </div>
          )}

          {detailLoading ? (
            <div className="space-y-2">{[1,2,3,4,5].map((i) => <div key={i} className="skeleton h-16 rounded-xl" />)}</div>
          ) : (
            <div className="space-y-2">
              {filtered.map(({ control, domain }) => <PolicyCard key={control.id} control={control} domain={domain} />)}
              {filtered.length === 0 && search && (
                <div className="bg-white border border-slate-100 rounded-2xl py-12 text-center" style={{ boxShadow: "var(--shadow-card)" }}>
                  <p className="text-sm text-slate-500">No policies match "{search}"</p>
                  <button onClick={() => setSearch("")} className="text-xs text-blue-600 hover:underline mt-2">Clear search</button>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
