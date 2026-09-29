"use client";

import { useEffect, useState, useCallback } from "react";
import { Upload, Trash2, RefreshCw, ChevronDown, ChevronUp, BookOpen, LayoutGrid, GitBranch } from "lucide-react";
import { api } from "@/lib/api";
import { canDelete, canCreate } from "@/lib/auth";
import FrameworkExplorer from "./FrameworkExplorer";

function FrameworkCard({ fw, onDeleted, role }) {
  const [expanded,      setExpanded]      = useState(false);
  const [detail,        setDetail]        = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [deleting,      setDeleting]      = useState(false);

  const loadDetail = async () => {
    if (detail) { setExpanded(v => !v); return; }
    setLoadingDetail(true);
    setExpanded(true);
    try { setDetail(await api.frameworks.get(fw.id)); } catch (e) { console.error(e); }
    finally { setLoadingDetail(false); }
  };

  const handleDelete = async (e) => {
    e.stopPropagation();
    if (!confirm(`Delete "${fw.name}"? This cannot be undone.`)) return;
    setDeleting(true);
    try { await api.frameworks.delete(fw.id); onDeleted(fw.id); }
    catch (e) { alert(e.message); setDeleting(false); }
  };

  return (
    <div className="bg-white border border-slate-100 rounded-2xl overflow-hidden" style={{ boxShadow: "var(--shadow-card)" }}>
      <div className="p-5">
        <div className="flex items-start justify-between gap-2 mb-3">
          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-bold text-slate-900 truncate">{fw.name}</h3>
            <p className="text-[10px] font-mono text-slate-400 mt-0.5">{fw.code}</p>
          </div>
          <span className="badge badge-neutral shrink-0">v{fw.version}</span>
        </div>
        <div className="grid grid-cols-2 gap-2 mb-4">
          <div className="text-center py-2 bg-blue-50/60 rounded-xl">
            <p className="text-lg font-bold text-blue-800 tabular-nums">{fw.total_domains}</p>
            <p className="text-[10px] text-blue-500">Domains</p>
          </div>
          {fw.total_categories > 0 ? (
            <div className="text-center py-2 bg-indigo-50/60 rounded-xl">
              <p className="text-lg font-bold text-indigo-800 tabular-nums">{fw.total_categories}</p>
              <p className="text-[10px] text-indigo-500">Subdomains</p>
            </div>
          ) : (
            <div className="text-center py-2 bg-slate-50 rounded-xl opacity-40">
              <p className="text-lg font-bold text-slate-400 tabular-nums">—</p>
              <p className="text-[10px] text-slate-400">Subdomains</p>
            </div>
          )}
          <div className="text-center py-2 bg-slate-50 rounded-xl">
            <p className="text-lg font-bold text-slate-900 tabular-nums">{fw.total_controls}</p>
            <p className="text-[10px] text-slate-400">Controls</p>
          </div>
          <div className="text-center py-2 bg-slate-50 rounded-xl">
            <p className="text-lg font-bold text-slate-900 tabular-nums">{fw.total_questions}</p>
            <p className="text-[10px] text-slate-400">Questions</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="badge badge-success">Active</span>
          <button onClick={loadDetail}
            className="ml-auto flex items-center gap-1.5 text-xs font-semibold text-blue-600 hover:text-blue-700 transition-colors">
            {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            {expanded ? "Collapse" : "Explore"}
          </button>
          {canDelete(role, "framework") && (
            <button onClick={handleDelete} disabled={deleting}
              className="p-1.5 rounded-lg text-slate-300 hover:text-rose-500 hover:bg-rose-50 transition-all">
              <Trash2 size={13} />
            </button>
          )}
        </div>
      </div>
      {expanded && (
        <div className="border-t border-slate-100 max-h-[480px] overflow-y-auto">
          {loadingDetail ? (
            <div className="space-y-2 p-4">{[1,2,3].map(i => <div key={i} className="skeleton h-10 rounded-xl" />)}</div>
          ) : (
            <FrameworkExplorer framework={detail} />
          )}
        </div>
      )}
    </div>
  );
}

export default function FrameworksView({ role }) {
  const [frameworks,      setFrameworks]      = useState([]);
  const [loading,         setLoading]         = useState(true);
  const [seeding,         setSeeding]         = useState(false);
  const [uploading,       setUploading]       = useState(false);
  const [msg,             setMsg]             = useState(null);
  const [viewMode,        setViewMode]        = useState("cards");
  const [explorerFw,      setExplorerFw]      = useState(null);
  const [explorerDetail,  setExplorerDetail]  = useState(null);
  const [explorerLoading, setExplorerLoading] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    api.frameworks.list().then(setFrameworks).catch(console.error).finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);

  const handleUpload = async (file) => {
    if (!file) return;
    setUploading(true); setMsg(null);
    try {
      const res = file.name.endsWith(".pdf") ? await api.frameworks.upload(file) : await api.frameworks.importJson(file);
      setMsg({ ok: true, text: `Imported ${res.framework_name} — ${res.controls} controls, ${res.domains} domains` });
      load();
    } catch (e) { setMsg({ ok: false, text: e.message }); } finally { setUploading(false); }
  };

  const selectExplorer = useCallback(async (fw) => {
    if (explorerFw?.id === fw.id) return;
    setExplorerFw(fw);
    setExplorerDetail(null);
    setExplorerLoading(true);
    try { setExplorerDetail(await api.frameworks.get(fw.id)); }
    catch (e) { console.error(e); } finally { setExplorerLoading(false); }
  }, [explorerFw?.id]);

  const handleViewMode = (mode) => {
    setViewMode(mode);
    if (mode === "explorer" && !explorerFw && frameworks.length > 0) selectExplorer(frameworks[0]);
  };

  const seedFromDisk = async () => {
    setSeeding(true); setMsg(null);
    try {
      const res = await api.frameworks.loadExisting();
      setMsg({ ok: true, text: `Imported ${res.imported} framework(s) from disk` });
      load();
    } catch (e) { setMsg({ ok: false, text: e.message }); } finally { setSeeding(false); }
  };

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-5 fade-in-up">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Frameworks</h2>
          <p className="text-sm text-slate-500 mt-1">NIST CSF · ISO 27001 · CIS Controls · Market Assessment</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center bg-slate-100 rounded-lg p-0.5">
            <button onClick={() => handleViewMode("cards")}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold transition-all ${viewMode === "cards" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-700"}`}>
              <LayoutGrid size={12} /> Cards
            </button>
            <button onClick={() => handleViewMode("explorer")}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold transition-all ${viewMode === "explorer" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-700"}`}>
              <GitBranch size={12} /> Explorer
            </button>
          </div>
        </div>
        {canCreate(role, "framework") && (
          <div className="flex items-center gap-2">
            <button onClick={seedFromDisk} disabled={seeding} className="btn-secondary">
              <RefreshCw size={13} className={seeding ? "animate-spin" : ""} />
              {seeding ? "Loading…" : "Load from Disk"}
            </button>
            <label className={`btn-primary cursor-pointer ${uploading ? "opacity-50 pointer-events-none" : ""}`}>
              <Upload size={13} /> {uploading ? "Uploading…" : "Upload"}
              <input type="file" accept=".pdf,.json" className="hidden" onChange={e => handleUpload(e.target.files[0])} disabled={uploading} />
            </label>
          </div>
        )}
      </div>

      {msg && (
        <div className={`flex items-center gap-2 text-sm px-4 py-3 rounded-xl border ${msg.ok ? "bg-emerald-50 border-emerald-200 text-emerald-700" : "bg-rose-50 border-rose-200 text-rose-700"}`}>
          {msg.text}
        </div>
      )}

      {viewMode === "cards" ? (
        loading ? (
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
            {[1,2,3].map(i => <div key={i} className="skeleton h-48 rounded-2xl" />)}
          </div>
        ) : frameworks.length === 0 ? (
          <div className="bg-white border border-slate-100 rounded-2xl py-20 text-center" style={{ boxShadow: "var(--shadow-card)" }}>
            <BookOpen size={28} className="text-slate-200 mx-auto mb-4" />
            <p className="text-sm font-semibold text-slate-500 mb-1">No frameworks loaded</p>
            <p className="text-xs text-slate-400 max-w-xs mx-auto">Click "Load from Disk" to import bundled frameworks, or upload a PDF/JSON file.</p>
          </div>
        ) : (
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
            {frameworks.map(fw => (
              <FrameworkCard key={fw.id} fw={fw} role={role}
                onDeleted={id => setFrameworks(p => p.filter(f => f.id !== id))} />
            ))}
          </div>
        )
      ) : (
        <div className="bg-white border border-slate-100 rounded-2xl overflow-hidden" style={{ boxShadow: "var(--shadow-card)" }}>
          {loading ? (
            <div className="p-5 space-y-3">{[1,2,3].map(i => <div key={i} className="skeleton h-12 rounded-xl" />)}</div>
          ) : frameworks.length === 0 ? (
            <p className="px-5 py-8 text-xs text-slate-400 text-center">No frameworks loaded.</p>
          ) : (
            <>
              <div className="flex gap-1 px-4 py-3 border-b border-slate-100 overflow-x-auto">
                {frameworks.map(fw => (
                  <button key={fw.id} onClick={() => selectExplorer(fw)}
                    className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all whitespace-nowrap ${
                      explorerFw?.id === fw.id ? "bg-blue-700 text-white" : "text-slate-500 hover:bg-slate-100"
                    }`}>
                    {fw.code}
                  </button>
                ))}
              </div>
              {explorerLoading ? (
                <div className="p-5 space-y-3">{[1,2,3].map(i => <div key={i} className="skeleton h-12 rounded-xl" />)}</div>
              ) : (
                <FrameworkExplorer framework={explorerDetail} />
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
