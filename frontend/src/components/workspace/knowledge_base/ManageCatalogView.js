"use client";

import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { Search, ShieldCheck, ListTree, Database, X, Layers, Lock } from "lucide-react";
import { api } from "@/lib/api";
import { canCreate } from "@/lib/auth";
import CatalogTree from "./CatalogTree";
import ControlEditor from "./ControlEditor";
import ValidationPanel from "./ValidationPanel";

function StatChip({ label, value, tone = "slate" }) {
  const tones = {
    slate: "bg-slate-50 text-slate-700",
    blue: "bg-blue-50 text-blue-700",
    amber: "bg-amber-50 text-amber-700",
  };
  return (
    <div className={`px-3 py-1.5 rounded-xl ${tones[tone]} flex items-center gap-2`}>
      <span className="text-base font-bold tabular-nums leading-none">{value}</span>
      <span className="text-[10px] uppercase tracking-wide font-semibold opacity-70">{label}</span>
    </div>
  );
}

function CreateControlModal({ frameworkId, category, onClose, onCreated }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [statement, setStatement] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);

  const submit = async () => {
    if (!code.trim()) { setErr("Control code is required."); return; }
    setSaving(true); setErr(null);
    try {
      const created = await api.catalog.control.create(frameworkId, {
        category_id: category.id, code: code.trim(), name: name.trim() || null,
        statement: statement.trim() || null, weight: 1, criticality: "medium", questions: [],
      });
      onCreated(created);
    } catch (e) { setErr(e.message); setSaving(false); }
  };

  const inputCls = "w-full text-sm border border-slate-200 rounded-xl px-3.5 py-2.5 outline-none focus:border-blue-400";
  return (
    <div className="fixed inset-0 z-50 bg-slate-900/40 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl w-full max-w-md p-5 space-y-3" onClick={e => e.stopPropagation()} style={{ boxShadow: "var(--shadow-elevated)" }}>
        <div className="flex items-center gap-2">
          <Layers size={15} className="text-indigo-500" />
          <h3 className="text-sm font-bold text-slate-900">New control</h3>
          <span className="text-[11px] text-slate-400 ml-1">in {category.name || category.code}</span>
          <button onClick={onClose} className="ml-auto text-slate-400 hover:text-slate-600"><X size={16} /></button>
        </div>
        {err && <p className="text-xs text-rose-600 bg-rose-50 border border-rose-200 rounded-lg px-3 py-2">{err}</p>}
        <div>
          <label className="block text-[11px] font-semibold text-slate-500 mb-1">Control code *</label>
          <input className={inputCls} value={code} onChange={e => setCode(e.target.value)} placeholder="e.g. AC-99" autoFocus />
        </div>
        <div>
          <label className="block text-[11px] font-semibold text-slate-500 mb-1">Name</label>
          <input className={inputCls} value={name} onChange={e => setName(e.target.value)} />
        </div>
        <div>
          <label className="block text-[11px] font-semibold text-slate-500 mb-1">Statement</label>
          <textarea rows={3} className={inputCls + " resize-y"} value={statement} onChange={e => setStatement(e.target.value)} />
        </div>
        <div className="flex items-center gap-2 justify-end pt-1">
          <button onClick={onClose} className="btn-secondary">Cancel</button>
          <button onClick={submit} disabled={saving} className="btn-primary">{saving ? "Creating…" : "Create control"}</button>
        </div>
      </div>
    </div>
  );
}

export default function ManageCatalogView({ role }) {
  const [frameworks, setFrameworks] = useState([]);
  const [fwId, setFwId]       = useState(null);
  const [fwDetail, setFwDetail] = useState(null);
  const [fwLoading, setFwLoading] = useState(false);
  const [controlPk, setControlPk] = useState(null);
  const [stats, setStats]    = useState(null);
  const [rightTab, setRightTab] = useState("tree");     // "tree" | "validation"
  const [createFor, setCreateFor] = useState(null);       // category object

  // global search
  const [q, setQ] = useState("");
  const [hits, setHits] = useState([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const searchTimer = useRef(null);

  const writable = canCreate(role, "framework");
  // Market Assessment frameworks are fully editable; others are questions-only.
  const fwEditable = useMemo(
    () => (frameworks.find(fw => fw.id === fwId)?.code ?? "").toUpperCase().includes("MARKET"),
    [frameworks, fwId]
  );
  const questionEditOnly = useMemo(
    () => writable && !fwEditable,
    [writable, fwEditable]
  );

  const loadStats = useCallback(() => {
    api.catalog.stats().then(setStats).catch(() => setStats(null));
  }, []);

  const loadFrameworks = useCallback(() => {
    api.frameworks.list().then(list => {
      setFrameworks(list);
      // Default to the Market Assessment framework so the editable one is shown first.
      const market = list.find(fw => fw.code.toUpperCase().includes("MARKET"));
      setFwId(prev => prev || (market?.id ?? list[0]?.id ?? null));
    }).catch(console.error);
  }, []);

  useEffect(() => { loadFrameworks(); loadStats(); }, [loadFrameworks, loadStats]);

  const loadFwDetail = useCallback((id, keepControl = false) => {
    if (!id) return;
    setFwLoading(true);
    api.frameworks.get(id)
      .then(d => { setFwDetail(d); if (!keepControl) setControlPk(null); })
      .catch(console.error)
      .finally(() => setFwLoading(false));
  }, []);

  useEffect(() => { if (fwId) loadFwDetail(fwId); }, [fwId, loadFwDetail]);

  // ── search (debounced) ──
  const runSearch = (val) => {
    setQ(val);
    if (searchTimer.current) clearTimeout(searchTimer.current);
    if (!val.trim()) { setHits([]); setSearchOpen(false); return; }
    searchTimer.current = setTimeout(() => {
      api.catalog.search(val.trim(), 20).then(res => { setHits(res); setSearchOpen(true); }).catch(() => setHits([]));
    }, 220);
  };

  const openHit = async (hit) => {
    setSearchOpen(false); setQ("");
    if (hit.framework_id !== fwId) {
      setFwId(hit.framework_id);
      const d = await api.frameworks.get(hit.framework_id);
      setFwDetail(d);
    }
    setRightTab("tree");
    setControlPk(hit.control_id);
  };

  // ── mutation callbacks → refresh tree + stats ──
  const afterChange = () => { loadFwDetail(fwId, true); loadStats(); };
  const afterDelete = () => { setControlPk(null); loadFwDetail(fwId); loadStats(); };
  const afterCreateOrDup = (detail) => {
    setCreateFor(null);
    loadFwDetail(fwId, true);
    loadStats();
    setControlPk(detail.id);
    setRightTab("tree");
  };

  return (
    <div className="flex flex-col h-full fade-in-up">
      {/* Header: stats + search */}
      <div className="shrink-0 px-6 py-4 border-b border-slate-200 bg-white">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2 mr-2">
            <Database size={16} className="text-blue-700" />
            <h2 className="text-lg font-bold text-slate-900">Manage Catalog</h2>
            <span className="badge badge-neutral">Central Admin</span>
          </div>
          {stats && (
            <div className="flex items-center gap-2">
              <StatChip label="Frameworks" value={stats.frameworks} tone="blue" />
              <StatChip label="Controls" value={stats.controls} />
              <StatChip label="Questions" value={stats.questions} />
              <StatChip label="No Questions" value={stats.controls_without_questions} tone={stats.controls_without_questions > 0 ? "amber" : "slate"} />
            </div>
          )}
          <div className="relative ml-auto w-72">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              value={q}
              onChange={e => runSearch(e.target.value)}
              onFocus={() => hits.length && setSearchOpen(true)}
              placeholder="Search all controls…"
              className="w-full text-sm border border-slate-200 rounded-xl pl-9 pr-8 py-2 outline-none focus:border-blue-400"
            />
            {q && <button onClick={() => runSearch("")} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"><X size={13} /></button>}
            {searchOpen && hits.length > 0 && (
              <div className="absolute z-40 mt-1 w-full max-h-80 overflow-y-auto bg-white border border-slate-200 rounded-xl py-1" style={{ boxShadow: "var(--shadow-elevated)" }}>
                {hits.map(h => (
                  <button key={h.control_id} onClick={() => openHit(h)}
                    className="w-full text-left px-3 py-2 hover:bg-blue-50/50 flex items-center gap-2">
                    <span className="badge badge-low font-mono text-[9px] shrink-0">{h.code}</span>
                    <span className="text-xs text-slate-600 flex-1 min-w-0 truncate">{h.name || h.statement || "—"}</span>
                    <span className="text-[9px] text-slate-400 font-mono shrink-0">{h.framework_code}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Body: framework rail | tree/validation | editor */}
      <div className="flex-1 flex min-h-0">
        {/* Framework rail */}
        <div className="w-48 shrink-0 border-r border-slate-200 bg-slate-50/50 overflow-y-auto p-2 space-y-1">
          {frameworks.map(fw => {
            const isMarket = fw.code.toUpperCase().includes("MARKET");
            const active = fwId === fw.id;
            return (
              <button key={fw.id} onClick={() => setFwId(fw.id)}
                className={`w-full text-left px-3 py-2 rounded-lg transition-colors ${
                  active ? "bg-blue-700 text-white" : "hover:bg-slate-100 text-slate-600"
                }`}>
                <div className="flex items-center gap-1.5">
                  <p className="text-xs font-bold truncate flex-1">{fw.code}</p>
                  {!isMarket && <Lock size={10} className={active ? "text-blue-200" : "text-slate-300"} />}
                </div>
                <p className={`text-[10px] truncate ${active ? "text-blue-100" : "text-slate-400"}`}>{fw.total_controls} controls</p>
              </button>
            );
          })}
        </div>

        {/* Middle: tree / validation with toggle */}
        <div className="flex-1 min-w-0 flex flex-col border-r border-slate-200">
          <div className="shrink-0 flex items-center gap-1 px-3 py-2 border-b border-slate-200 bg-white">
            <button onClick={() => setRightTab("tree")}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${rightTab === "tree" ? "bg-slate-100 text-slate-800" : "text-slate-500 hover:bg-slate-50"}`}>
              <ListTree size={13} /> Explorer
            </button>
            <button onClick={() => setRightTab("validation")}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${rightTab === "validation" ? "bg-slate-100 text-slate-800" : "text-slate-500 hover:bg-slate-50"}`}>
              <ShieldCheck size={13} /> Validation
              {stats?.validation_error_frameworks > 0 && <span className="badge badge-critical text-[9px]">{stats.validation_error_frameworks}</span>}
            </button>
          </div>
          <div className="flex-1 min-h-0">
            {fwLoading ? (
              <div className="p-4 space-y-2">{[1,2,3,4].map(i => <div key={i} className="skeleton h-10 rounded-xl" />)}</div>
            ) : rightTab === "tree" ? (
              <CatalogTree
                framework={fwDetail}
                selectedPk={controlPk}
                onSelect={setControlPk}
                onAddControl={(catId) => {
                  const cat = fwDetail.domains.flatMap(d => d.categories).find(c => c.id === catId);
                  if (cat) setCreateFor(cat);
                }}
                canEdit={writable && fwEditable}
                canAddControls={writable && fwEditable}
              />
            ) : (
              <ValidationPanel frameworkId={fwId} onJump={(cid) => { setRightTab("tree"); setControlPk(cid); }} />
            )}
          </div>
        </div>

        {/* Editor pane */}
        <div className="w-[42%] shrink-0 flex flex-col bg-white min-w-0">
          {controlPk ? (
            <ControlEditor
              key={controlPk}
              pk={controlPk}
              role={role}
              frameworkEditable={fwEditable}
              questionEditOnly={questionEditOnly}
              onChanged={afterChange}
              onDeleted={afterDelete}
              onDuplicated={afterCreateOrDup}
            />
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center text-center p-8">
              <ListTree size={30} className="text-slate-200 mb-4" />
              <p className="text-sm font-semibold text-slate-500 mb-1">Select a control to edit</p>
              <p className="text-xs text-slate-400 max-w-xs">Pick a control from the Explorer, or use the global search above to jump straight to one.</p>
            </div>
          )}
        </div>
      </div>

      {createFor && (
        <CreateControlModal
          frameworkId={fwId}
          category={createFor}
          onClose={() => setCreateFor(null)}
          onCreated={afterCreateOrDup}
        />
      )}
    </div>
  );
}
