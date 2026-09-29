"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import {
  Activity, Lock, Search, Calendar, RefreshCw, AlertTriangle,
  ChevronLeft, ChevronRight, X, MapPin, Cpu,
} from "lucide-react";
import { api } from "@/lib/api";
import { formatDateParts, istDayBoundsToUtcIso, DISPLAY_TZ_LABEL } from "@/lib/datetime";

const INPUT_CLS =
  "w-full text-sm border border-slate-200 rounded-xl px-4 py-2.5 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-50 transition-all text-slate-900 placeholder:text-slate-400 bg-white";

const PAGE_SIZE = 50;

// Event names are dotted and namespaced ("ai.control_score"). Tint the chip by
// its namespace so a long log is scannable without reading every row. Colours are
// the app's existing accents (see DashboardView) as light tint + matching text +
// matching border — the same badge recipe globals.css uses.
const NS_STYLE = {
  ai:            "bg-indigo-50 text-indigo-700 border-indigo-200",
  assessment:    "bg-emerald-50 text-emerald-700 border-emerald-200",
  user:          "bg-blue-50 text-blue-700 border-blue-200",
  owner:         "bg-blue-50 text-blue-700 border-blue-200",
  report:        "bg-violet-50 text-violet-700 border-violet-200",
  document:      "bg-amber-50 text-amber-700 border-amber-200",
  evidence:      "bg-cyan-50 text-cyan-700 border-cyan-200",
  questionnaire: "bg-cyan-50 text-cyan-700 border-cyan-200",
  catalog:       "bg-slate-100 text-slate-600 border-slate-200",
};
const NEUTRAL_STYLE = "bg-slate-100 text-slate-600 border-slate-200";
// Destructive / negative outcomes read rose regardless of namespace, matching how
// the rest of the app signals them.
const NEGATIVE = /(delete|deleted|reject|rejected|removed|revoked|failed)$/;

const nsOf = (event) => (event || "").split(".")[0];
const eventStyle = (event) =>
  NEGATIVE.test(event || "")
    ? "bg-rose-50 text-rose-700 border-rose-200"
    : NS_STYLE[nsOf(event)] || NEUTRAL_STYLE;

function EventChip({ event }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-[11px] font-semibold font-mono whitespace-nowrap ${eventStyle(event)}`}>
      {event}
    </span>
  );
}

export default function ActivityLogView() {
  const [rows,    setRows]    = useState([]);
  const [total,   setTotal]   = useState(0);
  const [facets,  setFacets]  = useState({ actions: [], markets: [] });
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);

  // `search` is what the user types; `q` is the debounced value we actually query
  // with, so typing does not fire a request per keystroke.
  const [search, setSearch] = useState("");
  const [q,      setQ]      = useState("");
  const [action, setAction] = useState("");
  const [market, setMarket] = useState("");
  const [from,   setFrom]   = useState("");
  const [to,     setTo]     = useState("");
  const [page,   setPage]   = useState(0);
  // Bumped to force a refetch with unchanged filters (the Refresh button).
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const next = search.trim();
    // Bail out when the debounced value has not actually changed. Without this
    // the timer still fired ~300ms after mount and set loading back to true,
    // while q/page stayed identical — so the fetch effect never re-ran to clear
    // it and the table sat on "Loading events…" forever with the data already in.
    if (next === q) return;
    const t = setTimeout(() => {
      // Any filter change restarts paging — page 4 of the previous result set is
      // meaningless once the filters move.
      setQ(next);
      setPage(0);
      setLoading(true);
    }, 300);
    return () => clearTimeout(t);
  }, [search, q]);

  // Filter setters that reset paging and flip the loading state themselves, so no
  // effect has to chase them (and React never re-renders twice to catch up).
  const applyFilter = useCallback(
    (setter) => (value) => { setter(value); setPage(0); setLoading(true); },
    [],
  );
  const onAction = applyFilter(setAction);
  const onMarket = applyFilter(setMarket);
  const onFrom   = applyFilter(setFrom);
  const onTo     = applyFilter(setTo);
  const goToPage = (next) => { setPage(next); setLoading(true); };

  useEffect(() => {
    // `cancelled` drops a response that a newer request has already superseded —
    // without it a slow early query can overwrite the results of a later one.
    let cancelled = false;
    (async () => {
      try {
        const data = await api.activity.list({
          q, action, market,
          // Send the UTC instants that bound the picked day in the operator's
          // timezone — a bare "2026-07-27" would mean a UTC day and silently
          // drop everything between 00:00 and 05:30 IST.
          from: istDayBoundsToUtcIso(from, "start"),
          to: istDayBoundsToUtcIso(to, "end"),
          limit: PAGE_SIZE, offset: page * PAGE_SIZE,
        });
        if (cancelled) return;
        setRows(data.items || []);
        setTotal(data.total || 0);
        setError(null);
      } catch (e) {
        if (cancelled) return;
        setError(e.message);
        setRows([]);
        setTotal(0);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [q, action, market, from, to, page, reloadKey]);

  const refresh = () => { setLoading(true); setReloadKey((k) => k + 1); };

  useEffect(() => {
    let cancelled = false;
    api.activity.facets()
      .then((f) => {
        if (!cancelled) setFacets({ actions: f.actions || [], markets: f.markets || [] });
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const hasFilters = Boolean(q || action || market || from || to);
  const clearAll = () => {
    setSearch(""); setQ(""); setAction(""); setMarket(""); setFrom(""); setTo(""); setPage(0);
  };

  const firstRow = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const lastRow  = Math.min(total, page * PAGE_SIZE + rows.length);
  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);

  const actionOptions = useMemo(
    () => facets.actions.filter((a) => a.action),
    [facets.actions]
  );

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-5 fade-in-up">

      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2.5">
            <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Activity log</h2>
            <span className="badge badge-neutral" title="Entries are appended only — they can never be edited or deleted.">
              <Lock size={10} /> Immutable
            </span>
          </div>
          <p className="text-sm text-slate-500 mt-1 max-w-2xl">
            A permanent, chronological record of the major actions taken in the system.
            This log is read-only and cannot be edited or deleted.
          </p>
        </div>
        <button onClick={refresh} disabled={loading}
          className="btn-secondary text-xs flex items-center gap-1.5 shrink-0">
          <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="flex items-start gap-3 px-4 py-3 rounded-xl border bg-rose-50 border-rose-200 text-rose-700 text-sm">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>Could not load the activity log. {error}</span>
        </div>
      )}

      {/* Filters */}
      <div className="bg-white border border-slate-100 rounded-2xl p-4 space-y-3"
        style={{ boxShadow: "var(--shadow-card)" }}>
        <div className="flex flex-col lg:flex-row lg:items-end gap-3">
          <div className="flex-1 min-w-0">
            <label className="block text-[10px] font-bold text-slate-400 mb-1.5 uppercase tracking-wider">Search</label>
            <div className="relative">
              <Search size={14} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-300 pointer-events-none" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search by actor, action, or target…"
                className={`${INPUT_CLS} pl-9`}
              />
              {search && (
                <button onClick={() => setSearch("")} title="Clear search"
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-300 hover:text-slate-600 transition-colors">
                  <X size={14} />
                </button>
              )}
            </div>
          </div>

          <div className="w-full lg:w-48">
            <label className="block text-[10px] font-bold text-slate-400 mb-1.5 uppercase tracking-wider">Event</label>
            <select value={action} onChange={(e) => onAction(e.target.value)} className={INPUT_CLS}>
              <option value="">All events</option>
              {actionOptions.map((a) => (
                <option key={a.action} value={a.action}>{a.action} ({a.count})</option>
              ))}
            </select>
          </div>

          <div className="w-full lg:w-40">
            <label className="block text-[10px] font-bold text-slate-400 mb-1.5 uppercase tracking-wider">Market</label>
            <select value={market} onChange={(e) => onMarket(e.target.value)} className={INPUT_CLS}>
              <option value="">All markets</option>
              {facets.markets.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>

          <div className="w-full lg:w-36">
            <label className="block text-[10px] font-bold text-slate-400 mb-1.5 uppercase tracking-wider">From</label>
            <input type="date" value={from} max={to || undefined}
              onChange={(e) => onFrom(e.target.value)} className={INPUT_CLS} />
          </div>

          <div className="w-full lg:w-36">
            <label className="block text-[10px] font-bold text-slate-400 mb-1.5 uppercase tracking-wider">To</label>
            <input type="date" value={to} min={from || undefined}
              onChange={(e) => onTo(e.target.value)} className={INPUT_CLS} />
          </div>
        </div>

        <div className="flex items-center justify-between pt-0.5">
          <p className="text-[11px] text-slate-400 flex items-center gap-1.5">
            <Calendar size={11} className="text-slate-300" />
            {loading
              ? "Loading events…"
              : total === 0
                ? "No matching events"
                : <>Showing <span className="font-semibold text-slate-600 tabular-nums">{firstRow}–{lastRow}</span> of <span className="font-semibold text-slate-600 tabular-nums">{total}</span> event{total !== 1 ? "s" : ""}</>}
          </p>
          {hasFilters && (
            <button onClick={clearAll}
              className="text-[11px] font-semibold text-blue-600 hover:text-blue-700 flex items-center gap-1 transition-colors">
              <X size={11} /> Clear filters
            </button>
          )}
        </div>
      </div>

      {/* Log */}
      <div className="bg-white border border-slate-100 rounded-2xl overflow-hidden"
        style={{ boxShadow: "var(--shadow-card)" }}>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-slate-100 bg-slate-50/60">
                {[`Date (${DISPLAY_TZ_LABEL})`, "Actor", "Market", "Event", "Details"].map((h) => (
                  <th key={h} className="text-left px-5 py-3 text-[11px] font-bold text-slate-400 uppercase tracking-wider whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                Array.from({ length: 8 }).map((_, i) => (
                  <tr key={i} className="border-b border-slate-50 last:border-0">
                    <td className="px-5 py-4"><span className="skeleton block h-4 w-20 rounded" /></td>
                    <td className="px-5 py-4"><span className="skeleton block h-4 w-32 rounded" /></td>
                    <td className="px-5 py-4"><span className="skeleton block h-4 w-16 rounded" /></td>
                    <td className="px-5 py-4"><span className="skeleton block h-4 w-28 rounded" /></td>
                    <td className="px-5 py-4"><span className="skeleton block h-4 w-full rounded" /></td>
                  </tr>
                ))
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={5} className="py-16 text-center">
                    <Activity size={28} className="text-slate-200 mx-auto mb-3" />
                    <p className="text-sm font-semibold text-slate-500 mb-1">
                      {hasFilters ? "No events match these filters" : "No activity recorded yet"}
                    </p>
                    <p className="text-xs text-slate-400">
                      {hasFilters
                        ? "Try a broader search or widen the date range."
                        : "Major actions are appended here as they happen."}
                    </p>
                    {hasFilters && (
                      <button onClick={clearAll} className="btn-secondary text-xs mt-4 inline-flex items-center gap-1.5">
                        <X size={12} /> Clear filters
                      </button>
                    )}
                  </td>
                </tr>
              ) : (
                rows.map((r) => {
                  const when = formatDateParts(r.date);
                  const isSystem = !r.actor_email;
                  return (
                    <tr key={r.id} className="border-b border-slate-50 last:border-0 hover:bg-slate-50/50 transition-colors align-top">
                      <td className="px-5 py-4 whitespace-nowrap">
                        <p className="text-sm font-semibold text-slate-900 tabular-nums">{when.date}</p>
                        <p className="text-xs text-slate-400 tabular-nums mt-0.5">{when.time}</p>
                      </td>
                      <td className="px-5 py-4 whitespace-nowrap">
                        {isSystem ? (
                          <span className="inline-flex items-center gap-1.5 text-sm font-medium text-slate-500">
                            <Cpu size={12} className="text-slate-300" />
                            {r.actor_name || "System"}
                          </span>
                        ) : (
                          <>
                            <p className="text-sm font-semibold text-slate-900">
                              {r.actor_name || r.actor_email}
                            </p>
                            <p className="text-xs text-slate-500 mt-0.5">{r.actor_email}</p>
                          </>
                        )}
                      </td>
                      <td className="px-5 py-4 whitespace-nowrap text-xs text-slate-500">
                        {r.market ? (
                          <span className="inline-flex items-center gap-1.5">
                            <MapPin size={11} className="text-amber-500 shrink-0" />{r.market}
                          </span>
                        ) : (
                          <span className="text-slate-300">—</span>
                        )}
                      </td>
                      <td className="px-5 py-4"><EventChip event={r.event} /></td>
                      <td className="px-5 py-4 text-xs text-slate-600 leading-relaxed min-w-[22rem]">
                        {r.details || <span className="text-slate-300">—</span>}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {total > PAGE_SIZE && (
          <div className="flex items-center justify-between px-5 py-3 border-t border-slate-100 bg-slate-50/40">
            <p className="text-[11px] text-slate-400">
              Page <span className="font-semibold text-slate-600 tabular-nums">{page + 1}</span> of{" "}
              <span className="font-semibold text-slate-600 tabular-nums">{lastPage + 1}</span>
            </p>
            <div className="flex items-center gap-2">
              <button onClick={() => goToPage(Math.max(0, page - 1))} disabled={page === 0 || loading}
                className="btn-secondary text-xs flex items-center gap-1">
                <ChevronLeft size={12} /> Newer
              </button>
              <button onClick={() => goToPage(Math.min(lastPage, page + 1))} disabled={page >= lastPage || loading}
                className="btn-secondary text-xs flex items-center gap-1">
                Older <ChevronRight size={12} />
              </button>
            </div>
          </div>
        )}
      </div>

      <p className="text-[11px] text-slate-400 flex items-center gap-1.5 px-1">
        <Lock size={10} className="text-slate-300" />
        Entries are appended automatically and retained permanently. No user or
        administrator can modify or remove a recorded event.
      </p>
    </div>
  );
}
