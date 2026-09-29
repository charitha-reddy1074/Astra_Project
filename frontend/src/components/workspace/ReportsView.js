"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Download, Printer, BarChart3, AlertTriangle, ShieldCheck, ListChecks,
  ChevronDown, ChevronUp, CheckCircle2, XCircle, Gauge, Layers, Sparkles, Globe,
  MessageCircleQuestion, Clock,
} from "lucide-react";
import { api } from "@/lib/api";
import { getIdentity, scopesToAssigned } from "@/lib/auth";

// Mature / Partial / Critical-Gap → app badge classes + bar colors.
const CAT_BADGE = { mature: "badge-success", partial: "badge-medium", critical_gap: "badge-critical" };
const CAT_LABEL = { mature: "Mature", partial: "Partial", critical_gap: "Critical Gap" };
const VERDICT_BADGE = { strong: "badge-success", partial: "badge-medium", weak: "badge-high", missing: "badge-critical" };

const fmt = (n) => Math.round(Number(n) || 0);
const barColor = (s) => (s >= 80 ? "#16a34a" : s >= 50 ? "#3b82f6" : "#dc2626");

function SectionCard({ children, className = "" }) {
  return (
    <div className={`bg-white border border-slate-100 rounded-2xl ${className}`} style={{ boxShadow: "var(--shadow-card)" }}>
      {children}
    </div>
  );
}

function SectionLabel({ icon: Icon, children, right }) {
  return (
    <div className="flex items-center gap-2 mb-4">
      {Icon && <Icon size={14} className="text-slate-400" />}
      <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">{children}</p>
      {right && <span className="ml-auto">{right}</span>}
    </div>
  );
}

function StatRow({ label, value, tone }) {
  const cls = {
    strong: "badge-success", partial: "badge-medium", weak: "badge-high",
    missing: "badge-critical", neutral: "badge-low",
  }[tone] || "badge-neutral";
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-slate-50 last:border-0">
      <span className="text-xs text-slate-500">{label}</span>
      <span className={`badge ${cls} font-bold tabular-nums`}>{value}</span>
    </div>
  );
}

function ScoreBar({ score }) {
  const s = fmt(score);
  return (
    <div className="flex items-center gap-2 min-w-[110px]">
      <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all" style={{ width: `${s}%`, background: barColor(s) }} />
      </div>
      <span className="text-xs font-bold text-slate-700 tabular-nums w-7 text-right">{s}</span>
    </div>
  );
}

function ReportDocument({ d, showAll, setShowAll, showAllMat, setShowAllMat, expandMarket }) {
  const sl = d.security_level || {};
  const stats = d.stats || {};
  const sig = d.signal_maturity || {};
  const reviews = d.enriched_reviews || [];
  const breakdown = d.maturity_breakdown || [];

  const PREVIEW = 6;
  const shown = showAll ? reviews : reviews.slice(0, PREVIEW);
  const MAT_PREVIEW = 6;
  const shownMat = showAllMat ? breakdown : breakdown.slice(0, MAT_PREVIEW);

  return (
    <div className="space-y-5">

      {/* Header */}
      <SectionCard className="overflow-hidden">
        <div className="band-ink px-6 py-5">
          <div className="flex items-center gap-2.5 mb-2">
            <ShieldCheck size={19} className="text-amber-400" />
            <h3 className="font-display text-lg font-extrabold text-white tracking-tight">Security Assessment Report</h3>
            <span className="ml-auto badge badge-low capitalize">{d.meta?.scoring_mode || "deterministic"}</span>
          </div>
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-slate-300">
            <span><span className="text-slate-400">Scope:</span> <b className="text-slate-100">{d.meta?.scope || "—"}</b></span>
            <span><span className="text-slate-400">Framework:</span> <b className="text-slate-100">{d.meta?.framework || "N/A"}</b></span>
            <span><span className="text-slate-400">Auditor:</span> <b className="text-slate-100">{d.meta?.auditor || "Unknown"}</b></span>
            <span><span className="text-slate-400">Date:</span> <b className="text-slate-100">{(d.meta?.answered_at || "").slice(0, 10) || "—"}</b></span>
          </div>
        </div>
      </SectionCard>

      {/* Dashboard row */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-5">
        {/* Security level gauge */}
        <SectionCard className="p-5 flex flex-col items-center text-center">
          <SectionLabel icon={Gauge}>Security Level</SectionLabel>
          <div className="score-ring my-1" style={{ width: 132, height: 132, background: `conic-gradient(${sl.color} ${(sl.score_degrees) || 0}deg, #e2e8f0 ${(sl.score_degrees) || 0}deg)` }}>
            <div className="text-center">
              <p className="text-3xl font-bold tabular-nums leading-none" style={{ color: sl.color }}>{fmt(sl.score)}%</p>
              <p className="text-[9px] text-slate-400 mt-1 uppercase tracking-wider">Score</p>
            </div>
          </div>
          <p className="text-base font-bold mt-3" style={{ color: sl.color }}>{sl.posture || sl.label}</p>
          <div className="flex flex-wrap gap-1.5 justify-center mt-2">
            <span className="badge badge-low">{sl.maturity}</span>
            <span className="badge badge-neutral" style={{ color: sl.color, borderColor: sl.color }}>{sl.risk} risk</span>
          </div>
        </SectionCard>

        {/* Top vulnerabilities */}
        <SectionCard className="p-5 lg:col-span-2">
          <SectionLabel icon={AlertTriangle}>Top Vulnerabilities / Gaps</SectionLabel>
          {(d.top_vulnerabilities || []).length > 0 ? (
            <ol className="space-y-2.5">
              {d.top_vulnerabilities.map((v, i) => (
                <li key={i} className="flex items-start gap-3 bg-rose-50/60 border border-rose-100 rounded-xl p-3">
                  <span className="shrink-0 w-6 h-6 rounded-full bg-rose-600 text-white text-xs font-bold flex items-center justify-center">{i + 1}</span>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-rose-900 leading-snug">{v.title || "Unanswered question"}</p>
                    {v.observation && <p className="text-xs text-rose-700/80 mt-0.5 leading-snug">{v.observation}</p>}
                  </div>
                  <span className="shrink-0 badge badge-critical font-bold tabular-nums">{fmt(v.score)}</span>
                </li>
              ))}
            </ol>
          ) : (
            <div className="flex items-center gap-2 bg-emerald-50 border border-emerald-100 rounded-xl px-4 py-3">
              <CheckCircle2 size={16} className="text-emerald-600" />
              <p className="text-sm font-semibold text-emerald-700">No critical vulnerabilities identified.</p>
            </div>
          )}
        </SectionCard>

        {/* At a glance */}
        <SectionCard className="p-5">
          <SectionLabel icon={ListChecks}>At a Glance</SectionLabel>
          <div>
            <StatRow label="Addressed" value={stats.strong || 0} tone="strong" />
            <StatRow label="Partial" value={stats.partial || 0} tone="partial" />
            <StatRow label="Weak" value={stats.weak || 0} tone="weak" />
            <StatRow label="Missing" value={stats.missing || 0} tone="missing" />
            {stats.yes_no_total > 0 && <StatRow label="Yes/No confirmed" value={`${stats.yes_no_positive}/${stats.yes_no_total}`} tone="strong" />}
            {stats.missing_evidence > 0 && <StatRow label="Evidence missing" value={stats.missing_evidence} tone="missing" />}
            {stats.lowest_maturity && <StatRow label="Maturity floor" value={stats.lowest_maturity} tone="weak" />}
            {stats.open_followup_count > 0 && (
              <StatRow label="Open follow-ups" value={stats.open_followup_count} tone="neutral" />
            )}
            <StatRow label="Scored questions" value={stats.total || 0} tone="neutral" />
          </div>
        </SectionCard>
      </div>

      {/* Summary banner */}
      {d.domain_summary && (
        <div className="bg-blue-50 border border-blue-100 rounded-2xl px-5 py-3.5 text-sm text-blue-900">
          <span className="font-semibold">Assessment summary:</span> {d.domain_summary}
        </div>
      )}

      
      {/* Open follow-up questions — separate from scored findings so they don't
          inflate the critical/weak verdict counts shown in the stats widgets. */}
      {(d.open_followups || []).length > 0 && (
        <SectionCard className="p-5">
          <SectionLabel icon={MessageCircleQuestion}
            right={<span className="badge badge-neutral">{d.open_followups.length} pending</span>}>
            Open Follow-up Questions
          </SectionLabel>
          <p className="text-xs text-slate-400 mb-3">
            These questions were generated by the AI to probe specific gaps. They are open items — not evidence of critical findings.
          </p>
          <div className="space-y-1.5">
            {d.open_followups.map((f, i) => (
              <div key={i} className="flex items-start gap-2 bg-slate-50 border border-slate-100 rounded-lg px-3 py-2">
                <Clock size={13} className="text-slate-400 mt-0.5 shrink-0" />
                <div className="min-w-0">
                  {f.control_id && <span className="text-[10px] font-mono font-semibold text-blue-600 mr-1.5">{f.control_id}</span>}
                  <span className="text-[11px] text-slate-700">{f.question_text}</span>
                </div>
              </div>
            ))}
          </div>
        </SectionCard>
      )}

      {/* AI evidence-rating summary (full detail lives in the AI Assistance tab) */}
      {d.ai_rating_summary && (
        <SectionCard className="p-5">
          <SectionLabel icon={Sparkles}
            right={<span className="badge badge-low">avg {fmt(d.ai_rating_summary.average_score)}</span>}>
            AI Evidence Rating — Summary
          </SectionLabel>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
            {[
              ["Categories rated", d.ai_rating_summary.categories_rated, "badge-neutral"],
              ["Strong", d.ai_rating_summary.by_verdict?.strong || 0, "badge-success"],
              ["Partial", d.ai_rating_summary.by_verdict?.partial || 0, "badge-medium"],
              ["Weak", d.ai_rating_summary.by_verdict?.weak || 0, "badge-high"],
            ].map(([label, value, cls]) => (
              <div key={label} className="text-center bg-slate-50 border border-slate-100 rounded-xl py-3">
                <p className="text-xl font-bold text-slate-900 tabular-nums">{value}</p>
                <span className={`badge ${cls} mt-1`}>{label}</span>
              </div>
            ))}
          </div>
          {/* For market assessments: show the 0-3 level distribution */}
          {d.market_analytics?.is_market && d.ai_rating_summary?.ai_level_distribution && (
            <div className="mb-3">
              <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2">AI-rated control levels (0-3 scale)</p>
              <div className="grid grid-cols-4 gap-2">
                {[
                  { level: 0, label: "Not in place",      color: "#dc2626" },
                  { level: 1, label: "Partially in place", color: "#f97316" },
                  { level: 2, label: "Passable",           color: "#3b82f6" },
                  { level: 3, label: "Strong",             color: "#16a34a" },
                ].map(({ level, label, color }) => {
                  const count = d.ai_rating_summary.ai_level_distribution[level] ?? 0;
                  return (
                    <div key={level} className="text-center bg-slate-50 border border-slate-100 rounded-xl py-2.5">
                      <p className="text-xl font-bold tabular-nums" style={{ color }}>{count}</p>
                      <p className="text-[9px] text-slate-400 mt-0.5">{level} · {label}</p>
                    </div>
                  );
                })}
              </div>
              <p className="text-[10px] text-slate-400 mt-2">These AI evidence ratings feed directly into the 0-3 maturity levels shown in the market analytics above.</p>
            </div>
          )}
          {(d.ai_rating_summary.weakest || []).length > 0 && (
            <div>
              <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2">Lowest-rated categories</p>
              <div className="space-y-1.5">
                {d.ai_rating_summary.weakest.map((c, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <span className="badge badge-neutral font-mono text-[10px]">{c.domain_code}</span>
                    <span className="text-xs text-slate-600 truncate flex-1">{c.category_name}</span>
                    <ScoreBar score={c.score} />
                  </div>
                ))}
              </div>
            </div>
          )}
          <p className="no-print text-[11px] text-slate-400 mt-3">Full ratings, gaps, and generated follow-up questions are in the assessment's AI Assistance tab.</p>
        </SectionCard>
      )}

      {/* Maturity classification — collapsible (hidden for market assessments) */}
      {breakdown.length > 0 && !d.market_analytics?.is_market && (
        <SectionCard className="p-5">
          <button onClick={() => setShowAllMat((v) => !v)} className="no-print w-full flex items-center gap-2 group">
            <Layers size={14} className="text-slate-400" />
            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Maturity Classification</p>
            <span className="badge badge-neutral ml-1">{breakdown.length}</span>
            <span className={`badge ${CAT_BADGE[sig.category] || "badge-neutral"} font-bold ml-1`}>{sig.category_label || "Partial"}</span>
            <span className="ml-auto flex items-center gap-1 text-xs font-semibold text-blue-600 group-hover:text-blue-700">
              {showAllMat ? <>Collapse <ChevronUp size={14} /></> : <>Expand all <ChevronDown size={14} /></>}
            </span>
          </button>
          <div className="print-only items-center gap-2 mb-2">
            <Layers size={14} className="text-slate-400" />
            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Maturity Classification</p>
            <span className="badge badge-neutral ml-1">{breakdown.length}</span>
            <span className={`badge ${CAT_BADGE[sig.category] || "badge-neutral"} font-bold ml-1`}>{sig.category_label || "Partial"}</span>
          </div>
          {sig.counts && (
            <p className="text-xs text-slate-400 mt-2">
              {sig.counts.mature || 0} mature · {sig.counts.partial || 0} partial · {sig.counts.critical_gap || 0} critical gap
            </p>
          )}
          <div className="overflow-x-auto mt-4">
            <table className="w-full text-sm report-table">
              <thead>
                <tr className="border-b border-slate-100">
                  {["Sub-topic / Control", "Classification", "Score", "Gaps revealed"].map((h) => (
                    <th key={h} className="text-left px-3 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-wider">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shownMat.map((b, i) => (
                  <tr key={i} className="border-b border-slate-50 last:border-0">
                    <td className="px-3 py-2.5 font-medium text-slate-700">{b.sub_topic || b.control_id}</td>
                    <td className="px-3 py-2.5"><span className={`badge ${CAT_BADGE[b.category] || "badge-neutral"}`}>{b.category_label}</span></td>
                    <td className="px-3 py-2.5 w-[140px]"><ScoreBar score={b.score} /></td>
                    <td className="px-3 py-2.5 text-xs text-slate-500">
                      {b.gaps && b.gaps.length > 0 ? (
                        <ul className="list-disc pl-4 space-y-0.5">{b.gaps.map((g, j) => <li key={j}>{g}</li>)}</ul>
                      ) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!showAllMat && breakdown.length > MAT_PREVIEW && (
            <button onClick={() => setShowAllMat(true)} className="no-print w-full mt-3 py-2 text-xs font-semibold text-blue-600 hover:bg-blue-50 rounded-xl transition-colors flex items-center justify-center gap-1">
              Show {breakdown.length - MAT_PREVIEW} more <ChevronDown size={14} />
            </button>
          )}
        </SectionCard>
      )}

      {/* Question-by-question — collapsible */}
      <SectionCard className="p-5">
        <button onClick={() => setShowAll((v) => !v)} className="no-print w-full flex items-center gap-2 group">
          <ListChecks size={14} className="text-slate-400" />
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Question-by-Question Analysis</p>
          <span className="badge badge-neutral ml-1">{reviews.length}</span>
          <span className="ml-auto flex items-center gap-1 text-xs font-semibold text-blue-600 group-hover:text-blue-700">
            {showAll ? <>Collapse <ChevronUp size={14} /></> : <>Expand all <ChevronDown size={14} /></>}
          </span>
        </button>
        <div className="print-only items-center gap-2 mb-2">
          <ListChecks size={14} className="text-slate-400" />
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Question-by-Question Analysis</p>
          <span className="badge badge-neutral ml-1">{reviews.length}</span>
        </div>

        <div className="overflow-x-auto mt-4">
          <table className="w-full text-sm report-table">
            <thead>
              <tr className="border-b border-slate-100">
                {["#", "Question", "Answer", "Observation", "Status", "Maturity", "Score"].map((h) => (
                  <th key={h} className="text-left px-3 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-wider whitespace-nowrap">{h}</th>
                ))}
                <th className="text-left px-3 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-wider whitespace-nowrap print-hide">Wt</th>
              </tr>
            </thead>
            <tbody>
              {shown.length === 0 ? (
                <tr><td colSpan={8} className="text-center py-8 text-slate-400 text-sm">No question reviews available.</td></tr>
              ) : shown.map((r, i) => {
                const verdict = String(r.verdict || "weak").toLowerCase();
                const resp = r.response;
                const respText = resp == null ? "—" : (typeof resp === "string" ? (resp.length > 100 ? resp.slice(0, 100) + "…" : (resp || "—")) : String(resp));
                return (
                  <tr key={i} className="border-b border-slate-50 last:border-0 hover:bg-slate-50/50">
                    <td className="px-3 py-3 text-xs text-slate-400 font-semibold tabular-nums align-top">{i + 1}</td>
                    <td className="px-3 py-3 text-slate-700 align-top max-w-[240px]">{r.question_text || "—"}</td>
                    <td className="px-3 py-3 text-slate-500 align-top max-w-[160px] break-words">{respText}</td>
                    <td className="px-3 py-3 text-xs text-slate-500 italic align-top max-w-[220px]">{r.observation}</td>
                    <td className="px-3 py-3 align-top"><span className={`badge ${VERDICT_BADGE[verdict] || "badge-neutral"} whitespace-nowrap`}>{r.gap_flag?.label || verdict}</span></td>
                    <td className="px-3 py-3 align-top">
                      {r.maturity_category_label
                        ? <span className={`badge ${CAT_BADGE[r.maturity_category] || "badge-neutral"} whitespace-nowrap`} title={r.maturity_expectation || ""}>{r.maturity_category_label}</span>
                        : <span className="text-slate-300">—</span>}
                    </td>
                    <td className="px-3 py-3 align-top"><ScoreBar score={r.score} /></td>
                    <td className="px-3 py-3 text-xs text-slate-400 text-center align-top print-hide">{r.weight}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {!showAll && reviews.length > PREVIEW && (
          <button onClick={() => setShowAll(true)} className="no-print w-full mt-3 py-2 text-xs font-semibold text-blue-600 hover:bg-blue-50 rounded-xl transition-colors flex items-center justify-center gap-1">
            Show {reviews.length - PREVIEW} more <ChevronDown size={14} />
          </button>
        )}
      </SectionCard>

      {/* Strengths & gaps */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5 print-2col">
        <SectionCard className="p-5">
          <div className="flex items-center gap-2 mb-3">
            <CheckCircle2 size={15} className="text-emerald-600" />
            <h3 className="text-sm font-bold text-emerald-700">Strengths</h3>
          </div>
          {(d.strengths || []).length > 0 ? (
            <ul className="space-y-2">
              {d.strengths.map((s, i) => (
                <li key={i} className="text-xs text-emerald-900 bg-emerald-50 border-l-2 border-emerald-500 rounded-r-lg px-3 py-2 leading-snug">{s}</li>
              ))}
            </ul>
          ) : <p className="text-xs text-slate-400 italic">No strongly addressed controls identified.</p>}
        </SectionCard>
        <SectionCard className="p-5">
          <div className="flex items-center gap-2 mb-3">
            <XCircle size={15} className="text-rose-600" />
            <h3 className="text-sm font-bold text-rose-700">Gaps &amp; Weaknesses</h3>
          </div>
          {(d.gaps || []).length > 0 ? (
            <ul className="space-y-2">
              {d.gaps.map((g, i) => (
                <li key={i} className="text-xs text-rose-900 bg-rose-50 border-l-2 border-rose-500 rounded-r-lg px-3 py-2 leading-snug">{g}</li>
              ))}
            </ul>
          ) : <p className="text-xs text-slate-400 italic">No significant gaps identified.</p>}
        </SectionCard>
      </div>

      {/* Score distribution */}
      {(d.score_distribution || []).length > 0 && (
        <SectionCard className="p-5">
          <SectionLabel icon={BarChart3}>Score Distribution</SectionLabel>
          <div className="flex items-end gap-3 h-28 mt-2">
            {d.score_distribution.map((b, i) => (
              <div key={i} className="flex-1 flex flex-col items-center justify-end h-full">
                <span className="text-xs font-bold text-slate-700 mb-1 tabular-nums">{b.count}</span>
                <div className="w-full rounded-t-lg bg-blue-600/90 min-h-[4px] transition-all" style={{ height: `${b.pct}%` }} />
                <span className="text-[10px] text-slate-400 mt-1.5 whitespace-nowrap">{b.range}</span>
              </div>
            ))}
          </div>
        </SectionCard>
      )}
    </div>
  );
}

// MarketReportDisplay and CombinedReportDisplay are retained as dead code (no longer rendered).

function MarketReportDisplay({ report }) {
  if (!report) return null;
  const bySev = report.findings_by_severity || {};
  const domainScores = report.domain_scores || [];
  const findings = report.findings || [];

  return (
    <div className="space-y-5">
      {/* Header card */}
      <SectionCard className="overflow-hidden">
        <div className="band-ink px-6 py-5">
          <p className="text-[10.5px] font-extrabold uppercase tracking-[0.16em] text-amber-400 mb-1.5">Market Report</p>
          <h2 className="font-display text-xl font-extrabold text-white tracking-tight">{report.market_label}</h2>
          <p className="text-sm text-amber-100/80 mt-1 font-medium">{report.assessment?.name}</p>
        </div>
        <div className="p-5 grid grid-cols-2 md:grid-cols-4 gap-4">
          {[
            { label: "Overall Score", value: report.overall_score != null ? `${report.overall_score}%` : "—", tone: "strong" },
            { label: "Maturity Level", value: report.maturity_level != null ? `${report.maturity_level}/5` : "—", tone: "partial" },
            { label: "Findings", value: findings.length, tone: findings.length > 0 ? "weak" : "strong" },
            { label: "Critical", value: bySev.critical || 0, tone: bySev.critical > 0 ? "missing" : "neutral" },
          ].map(({ label, value, tone }) => (
            <div key={label} className="text-center">
              <p className="text-[10px] text-slate-400 uppercase tracking-wider">{label}</p>
              <p className="text-2xl font-bold text-slate-900 tabular-nums mt-0.5">{value}</p>
            </div>
          ))}
        </div>
      </SectionCard>

      {/* Domain scores */}
      {domainScores.length > 0 && (
        <SectionCard className="p-5">
          <SectionLabel icon={BarChart3}>Domain Scores</SectionLabel>
          <div className="space-y-2">
            {domainScores.map(d => (
              <div key={d.domain_code} className="flex items-center gap-3">
                <span className="text-[11px] font-mono font-semibold text-slate-500 w-28 shrink-0 truncate">{d.domain_code}</span>
                <div className="flex-1 h-2 bg-slate-100 rounded-full overflow-hidden">
                  <div className="h-full rounded-full" style={{ width: `${Math.min(d.percentage, 100)}%`, background: barColor(d.percentage) }} />
                </div>
                <span className="text-xs font-bold text-slate-700 tabular-nums w-10 text-right">{fmt(d.percentage)}%</span>
                <span className="text-[10px] text-slate-400 w-8 text-right">L{d.maturity_level}</span>
              </div>
            ))}
          </div>
        </SectionCard>
      )}

      {/* Findings */}
      {findings.length > 0 && (
        <SectionCard className="p-5">
          <SectionLabel icon={AlertTriangle}>Findings ({findings.length})</SectionLabel>
          <div className="space-y-2">
            {findings.map((f, i) => (
              <div key={i} className="flex items-start gap-3 py-2 border-b border-slate-50 last:border-0">
                <span className={`badge text-[10px] shrink-0 ${
                  f.severity === "critical" ? "badge-critical" : f.severity === "high" ? "badge-high" :
                  f.severity === "medium" ? "badge-medium" : "badge-low"}`}>{f.severity}</span>
                <div className="min-w-0">
                  <p className="text-xs font-semibold text-slate-800 truncate">{f.title}</p>
                  {f.gap_description && <p className="text-[11px] text-slate-400 mt-0.5 line-clamp-2">{f.gap_description}</p>}
                </div>
              </div>
            ))}
          </div>
        </SectionCard>
      )}

      {/* Personnel */}
      {((report.assessors || []).length > 0 || (report.owners || []).length > 0) && (
        <SectionCard className="p-5">
          <SectionLabel icon={ShieldCheck}>Personnel</SectionLabel>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2">Assessors</p>
              {(report.assessors || []).map(e => (
                <p key={e} className="text-xs text-slate-600">{e}</p>
              ))}
            </div>
            <div>
              <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2">Owners</p>
              {(report.owners || []).map(e => (
                <p key={e} className="text-xs text-slate-600">{e}</p>
              ))}
            </div>
          </div>
        </SectionCard>
      )}
    </div>
  );
}

function CombinedReportDisplay({ report }) {
  if (!report) return null;
  const agg = report.aggregate || {};
  const markets = report.markets || [];
  const bySev = agg.findings_by_severity || {};
  const multiMarket = markets.length > 1;

  // ── Comparative helpers (only when 2+ markets) ──────────────────────────────
  // Market ranking: scored markets sorted highest → lowest.
  const ranked = multiMarket
    ? [...markets]
        .filter(m => m.overall_score != null)
        .sort((a, b) => b.overall_score - a.overall_score)
    : [];

  // Domain comparison matrix: { domainCode → { marketLabel → pct } }
  const domainMatrix = {};
  if (multiMarket) {
    markets.forEach(m => {
      (m.domain_scores || []).forEach(ds => {
        if (!domainMatrix[ds.domain_code]) domainMatrix[ds.domain_code] = {};
        domainMatrix[ds.domain_code][m.market_label] = ds.percentage;
      });
    });
  }
  const domainCodes = Object.keys(domainMatrix).sort();
  const marketLabels = markets.map(m => m.market_label);

  // Per-domain leader / laggard
  const domainLeaders = {};
  if (multiMarket) {
    domainCodes.forEach(code => {
      const entries = Object.entries(domainMatrix[code]);
      if (entries.length < 2) return;
      entries.sort((a, b) => b[1] - a[1]);
      domainLeaders[code] = { best: entries[0], worst: entries[entries.length - 1] };
    });
  }

  // Score gap = difference between best and worst market overall
  const scoreGap = ranked.length >= 2
    ? (ranked[0].overall_score - ranked[ranked.length - 1].overall_score).toFixed(1)
    : null;

  return (
    <div className="space-y-5">
      {/* Summary header */}
      <SectionCard className="overflow-hidden">
        <div className="band-ink px-6 py-5">
          <p className="text-[10.5px] font-extrabold uppercase tracking-[0.16em] text-amber-400 mb-1.5">Combined Report</p>
          <h2 className="font-display text-xl font-extrabold text-white tracking-tight">{report.assessment_name}</h2>
          <p className="text-sm text-amber-100/80 mt-1 font-medium">{report.scored_markets} of {report.total_markets} markets scored</p>
        </div>
        <div className="p-5 grid grid-cols-2 md:grid-cols-4 gap-4">
          {[
            { label: "Avg Score", value: report.combined_score != null ? `${report.combined_score}%` : "—" },
            { label: "Markets", value: report.total_markets },
            { label: "Total Findings", value: (agg.findings || []).length },
            { label: "Score Gap", value: scoreGap != null ? `${scoreGap}%` : "—" },
          ].map(({ label, value }) => (
            <div key={label} className="text-center">
              <p className="text-[10px] text-slate-400 uppercase tracking-wider">{label}</p>
              <p className="text-2xl font-bold text-slate-900 tabular-nums mt-0.5">{value}</p>
            </div>
          ))}
        </div>
      </SectionCard>

      {/* ── Comparative analysis (only when 2+ markets) ── */}
      {multiMarket && ranked.length > 0 && (
        <>
          {/* Market ranking */}
          <SectionCard className="p-5">
            <SectionLabel icon={BarChart3}>Market Ranking</SectionLabel>
            <div className="space-y-2.5">
              {ranked.map((m, i) => (
                <div key={m.assessment_market_id} className="flex items-center gap-3">
                  <span className={`text-[10px] font-bold w-5 text-center shrink-0 ${
                    i === 0 ? "text-emerald-600" : i === ranked.length - 1 ? "text-red-500" : "text-slate-400"
                  }`}>#{i + 1}</span>
                  <span className="text-xs font-semibold text-slate-700 w-28 shrink-0 truncate">{m.market_label}</span>
                  <div className="flex-1 h-2.5 bg-slate-100 rounded-full overflow-hidden">
                    <div className="h-full rounded-full transition-all"
                      style={{ width: `${Math.min(fmt(m.overall_score), 100)}%`, background: barColor(fmt(m.overall_score)) }} />
                  </div>
                  <span className="text-xs font-bold tabular-nums w-10 text-right" style={{ color: barColor(fmt(m.overall_score)) }}>
                    {fmt(m.overall_score)}%
                  </span>
                  <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full shrink-0 ${
                    m.status === "completed" ? "bg-emerald-50 text-emerald-700" :
                    m.status === "in_review" ? "bg-blue-50 text-blue-700" : "bg-amber-50 text-amber-700"
                  }`}>{m.status.replace("_", " ")}</span>
                </div>
              ))}
            </div>
          </SectionCard>

          {/* Domain × Market comparison table */}
          {domainCodes.length > 0 && (
            <SectionCard className="p-5">
              <SectionLabel icon={Layers}>Domain Comparison by Market</SectionLabel>
              <div className="overflow-x-auto">
                <table className="w-full text-xs report-table">
                  <thead>
                    <tr className="border-b border-slate-100 bg-slate-50">
                      <th className="text-left px-3 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-wider w-28">Domain</th>
                      {marketLabels.map(ml => (
                        <th key={ml} className="text-center px-3 py-2 text-[10px] font-bold text-slate-500 uppercase tracking-wider">{ml}</th>
                      ))}
                      <th className="text-center px-3 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-wider">Best</th>
                      <th className="text-center px-3 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-wider">Worst</th>
                      <th className="text-center px-3 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-wider">Gap</th>
                    </tr>
                  </thead>
                  <tbody>
                    {domainCodes.map(code => {
                      const row = domainMatrix[code];
                      const vals = Object.values(row).filter(v => v != null);
                      const bestVal = vals.length ? Math.max(...vals) : null;
                      const worstVal = vals.length ? Math.min(...vals) : null;
                      const gap = bestVal != null && worstVal != null ? (bestVal - worstVal).toFixed(1) : "—";
                      return (
                        <tr key={code} className="border-b border-slate-50 hover:bg-slate-50/60">
                          <td className="px-3 py-2.5 font-mono font-semibold text-slate-600 text-[11px]">{code}</td>
                          {marketLabels.map(ml => {
                            const pct = row[ml];
                            const isHighest = pct === bestVal && vals.length > 1;
                            const isLowest  = pct === worstVal && vals.length > 1;
                            return (
                              <td key={ml} className="px-3 py-2.5 text-center">
                                {pct != null ? (
                                  <span className={`text-xs font-bold tabular-nums px-1.5 py-0.5 rounded ${
                                    isHighest ? "bg-emerald-50 text-emerald-700" :
                                    isLowest  ? "bg-red-50 text-red-600" : "text-slate-700"
                                  }`}>{fmt(pct)}%</span>
                                ) : <span className="text-slate-300">—</span>}
                              </td>
                            );
                          })}
                          <td className="px-3 py-2.5 text-center text-[10px] font-semibold text-emerald-600">
                            {domainLeaders[code] ? domainLeaders[code].best[0] : "—"}
                          </td>
                          <td className="px-3 py-2.5 text-center text-[10px] font-semibold text-red-500">
                            {domainLeaders[code] ? domainLeaders[code].worst[0] : "—"}
                          </td>
                          <td className="px-3 py-2.5 text-center text-xs font-bold text-slate-500 tabular-nums">{gap}{gap !== "—" ? "%" : ""}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="text-[10px] text-slate-400 mt-2">
                <span className="inline-block w-2 h-2 rounded bg-emerald-100 mr-1" />Highest in row &nbsp;
                <span className="inline-block w-2 h-2 rounded bg-red-100 mr-1" />Lowest in row
              </p>
            </SectionCard>
          )}

          {/* Per-market findings summary */}
          <SectionCard className="p-5">
            <SectionLabel icon={AlertTriangle}>Findings by Market</SectionLabel>
            <div className="overflow-x-auto">
              <table className="w-full text-xs report-table">
                <thead>
                  <tr className="border-b border-slate-100 bg-slate-50">
                    {["Market", "Score", "Critical", "High", "Medium", "Low"].map(h => (
                      <th key={h} className="text-left px-3 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-wider">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {markets.map(m => (
                    <tr key={m.assessment_market_id} className="border-b border-slate-50 hover:bg-slate-50/60">
                      <td className="px-3 py-2.5 font-semibold text-slate-700">{m.market_label}</td>
                      <td className="px-3 py-2.5 font-bold tabular-nums" style={{ color: barColor(fmt(m.overall_score)) }}>
                        {m.overall_score != null ? `${fmt(m.overall_score)}%` : "—"}
                      </td>
                      <td className="px-3 py-2.5 tabular-nums text-red-600 font-semibold">{m.findings_by_severity?.critical ?? 0}</td>
                      <td className="px-3 py-2.5 tabular-nums text-orange-600">{m.findings_by_severity?.high ?? 0}</td>
                      <td className="px-3 py-2.5 tabular-nums text-amber-600">{m.findings_by_severity?.medium ?? 0}</td>
                      <td className="px-3 py-2.5 tabular-nums text-slate-400">{m.findings_by_severity?.low ?? 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </SectionCard>
        </>
      )}

      {/* Single-market summary table (when only 1 market scored, no comparison) */}
      {!multiMarket && markets.length > 0 && (
        <SectionCard className="p-5">
          <SectionLabel icon={Layers}>Market Summary</SectionLabel>
          <div className="overflow-x-auto">
            <table className="w-full text-xs report-table">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {["Market", "Status", "Score", "Critical", "High", "Medium"].map(h => (
                    <th key={h} className="text-left px-3 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-wider">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {markets.map(m => (
                  <tr key={m.assessment_market_id} className="border-b border-slate-50">
                    <td className="px-3 py-2.5 font-semibold text-slate-700">{m.market_label}</td>
                    <td className="px-3 py-2.5">
                      <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${
                        m.status === "completed" ? "bg-emerald-50 text-emerald-700" :
                        m.status === "in_review" ? "bg-blue-50 text-blue-700" : "bg-amber-50 text-amber-700"
                      }`}>{m.status.replace("_", " ")}</span>
                    </td>
                    <td className="px-3 py-2.5 font-bold text-slate-900 tabular-nums">
                      {m.overall_score != null ? `${fmt(m.overall_score)}%` : "—"}
                    </td>
                    <td className="px-3 py-2.5 tabular-nums text-red-600 font-semibold">{m.findings_by_severity?.critical ?? 0}</td>
                    <td className="px-3 py-2.5 tabular-nums text-orange-600">{m.findings_by_severity?.high ?? 0}</td>
                    <td className="px-3 py-2.5 tabular-nums text-amber-600">{m.findings_by_severity?.medium ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      )}

      {/* Aggregate domain scores */}
      {(agg.domain_scores || []).length > 0 && (
        <SectionCard className="p-5">
          <SectionLabel icon={BarChart3}>Aggregate Domain Scores (Average)</SectionLabel>
          <div className="space-y-2">
            {agg.domain_scores.map(d => (
              <div key={d.domain_code} className="flex items-center gap-3">
                <span className="text-[11px] font-mono font-semibold text-slate-500 w-28 shrink-0 truncate">{d.domain_code}</span>
                <div className="flex-1 h-2 bg-slate-100 rounded-full overflow-hidden">
                  <div className="h-full rounded-full" style={{ width: `${Math.min(d.percentage, 100)}%`, background: barColor(d.percentage) }} />
                </div>
                <span className="text-xs font-bold text-slate-700 tabular-nums w-10 text-right">{fmt(d.percentage)}%</span>
              </div>
            ))}
          </div>
        </SectionCard>
      )}

      {/* Merged findings */}
      {(agg.findings || []).length > 0 && (
        <SectionCard className="p-5">
          <SectionLabel icon={AlertTriangle}>All Findings ({agg.findings.length})</SectionLabel>
          <div className="space-y-2">
            {agg.findings.slice(0, 50).map((f, i) => (
              <div key={i} className="flex items-start gap-3 py-2 border-b border-slate-50 last:border-0">
                <span className={`badge text-[10px] shrink-0 ${
                  f.severity === "critical" ? "badge-critical" : f.severity === "high" ? "badge-high" :
                  f.severity === "medium" ? "badge-medium" : "badge-low"}`}>{f.severity}</span>
                <div className="min-w-0">
                  <p className="text-xs font-semibold text-slate-800 truncate">{f.title}</p>
                  {f.market_label && <p className="text-[10px] text-blue-500 mt-0.5">{f.market_label}</p>}
                </div>
              </div>
            ))}
          </div>
        </SectionCard>
      )}
    </div>
  );
}

export default function ReportsView({ setActiveView, role }) {
  const [assessments,    setAssessments]    = useState([]);
  const [selected,       setSelected]       = useState(null);
  const [report,         setReport]         = useState(null);
  const [loading,        setLoading]        = useState(true);
  const [reportLoading,  setReportLoading]  = useState(false);
  const [error,          setError]          = useState(null);
  const [showAll,        setShowAll]        = useState(false);
  const [showAllMat,     setShowAllMat]     = useState(false);
  const [showAllMarket,  setShowAllMarket]  = useState(false);

  useEffect(() => {
    api.assessments.list()
      .then(list => {
        // An Assessor only sees reports for the assessments assigned to them.
        const id = (getIdentity() || "").toLowerCase();
        const scoped = scopesToAssigned(role)
          ? list.filter(a => {
              if (a.assigned_to && a.assigned_to.toLowerCase() === id) return true;
              return false;
            })
          : list;
        // Reports are only available after an assessment has been submitted.
        const submitted = scoped.filter(a => a.status === "in_review" || a.status === "completed");
        setAssessments(submitted);
        const done = submitted.find(a => a.status === "completed") || submitted[0];
        if (done) setSelected(done);
      })
      .catch(console.error).finally(() => setLoading(false));
  }, [role]);

  useEffect(() => {
    if (!selected?.id) { setReport(null); return; }
    setReportLoading(true); setError(null);
    setShowAll(false); setShowAllMat(false); setShowAllMarket(false);
    api.assessments.reportView(selected.id)
      .then(setReport)
      .catch(e => { setError(e.message); setReport(null); })
      .finally(() => setReportLoading(false));
  }, [selected?.id]);

  // Print / Save PDF
  // Clone the report into a top-level <div>, inject @media print CSS that hides
  // everything else, then call window.print() on the current page. No iframe, no
  // popup — zero cross-origin frame access, zero SecurityError risk.
  const handlePrint = useCallback(() => {
    if (!report) return;

    setShowAll(true);
    setShowAllMat(true);
    setShowAllMarket(true);

    setTimeout(() => {
      const source = document.getElementById("report-content");
      if (!source) return;

      const printRoot = document.createElement("div");
      printRoot.id = "__print-root";
      printRoot.innerHTML = source.innerHTML;
      document.body.appendChild(printRoot);

      const style = document.createElement("style");
      style.id = "__print-style";
      style.textContent = `
        @media print {
          @page { size: A4 portrait; margin: 11mm 14mm; }
          body > *:not(#__print-root) { display: none !important; }
          #__print-root {
            display: block !important;
            position: static !important;
            width: 100% !important;
            margin: 0 !important;
            padding: 0 !important;
            font-size: 9pt;
            line-height: 1.4;
            color: #0f172a;
            background: #fff;
          }
          #__print-root #report-content { max-width: 100% !important; padding: 0 !important; margin: 0 !important; }
          .fade-in-up, .card-in { animation: none !important; opacity: 1 !important; transform: none !important; }
          .no-print, .print-hide { display: none !important; }
          .print-only { display: flex !important; }
          .grid { display: block !important; }
          .grid > * { display: block !important; width: 100% !important; margin-bottom: 8pt !important; }
          .print-2col { display: grid !important; grid-template-columns: 1fr 1fr !important; gap: 7pt !important; }
          .print-2col > * { margin-bottom: 0 !important; }
          .space-y-5 > * + * { margin-top: 9pt !important; }
          .space-y-4 > * + * { margin-top: 7pt !important; }
          .space-y-3 > * + * { margin-top: 5pt !important; }
          .overflow-x-auto { overflow: visible !important; }
          table { width: 100% !important; border-collapse: collapse !important; }
          td, th { word-break: break-word !important; overflow-wrap: break-word !important; }
          .report-table td, .report-table th { padding: 3pt 4pt !important; font-size: 8pt !important; line-height: 1.3 !important; }
          .report-table thead th { font-size: 7.5pt !important; }
          .p-6 { padding: 9pt !important; }
          .p-5 { padding: 7pt !important; }
          .p-4 { padding: 6pt !important; }
          .p-3 { padding: 4pt !important; }
          .px-6, .px-5 { padding-left: 8pt !important; padding-right: 8pt !important; }
          .py-5 { padding-top: 6pt !important; padding-bottom: 6pt !important; }
          .py-4 { padding-top: 5pt !important; padding-bottom: 5pt !important; }
          .space-y-5 > * { break-inside: auto !important; page-break-inside: auto !important; }
          tr, li { break-inside: avoid !important; page-break-inside: avoid !important; }
          thead { display: table-header-group !important; }
          .bg-linear-to-r, .band-ink { background: #172554 !important; background-image: none !important; }
          .band-ink::after { display: none !important; }
          * { box-shadow: none !important; -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
        }
      `;
      document.head.appendChild(style);

      const cleanup = () => {
        document.getElementById("__print-root")?.remove();
        document.getElementById("__print-style")?.remove();
      };

      window.onafterprint = cleanup;
      window.print();
      setTimeout(cleanup, 60000);
    }, 500);
  }, [report]);

  const exportJson = () => {
    if (!report) return;
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = `report-${(selected?.id || "report").slice(0, 8)}.json`; a.click();
  };

  if (loading) return (
    <div className="p-6 max-w-6xl mx-auto space-y-4">
      <div className="skeleton h-8 w-40 rounded" />
      <div className="skeleton h-64 rounded-2xl" />
    </div>
  );

  if (assessments.length === 0) return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="bg-white border border-slate-100 rounded-2xl py-20 text-center" style={{ boxShadow: "var(--shadow-card)" }}>
        <BarChart3 size={28} className="text-slate-200 mx-auto mb-4" />
        <p className="text-sm font-semibold text-slate-500 mb-1">No reports available</p>
        <p className="text-xs text-slate-400 mb-4">Reports are generated once an assessment has been submitted.</p>
        <button onClick={() => setActiveView?.("assessments")} className="btn-primary mx-auto">Go to Assessments</button>
      </div>
    </div>
  );

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-5 fade-in-up" id="report-content">
      <div className="flex items-center justify-between no-print">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Reports</h2>
          <p className="text-sm text-slate-500 mt-1">Security assessment report — Mature / Partial / Critical-Gap analysis.</p>
        </div>
        <div className="flex gap-2 no-print">
                    {report && (
            <button onClick={handlePrint} className="btn-secondary"><Printer size={13} /> Print / Save PDF</button>
          )}
          {report && (
            <button onClick={exportJson} className="btn-secondary"><Download size={13} /> Export JSON</button>
          )}
        </div>
      </div>

      {/* Assessment selector */}
      {assessments.length > 1 && (
        <div className="no-print flex gap-1.5 bg-white border border-slate-200 rounded-xl p-1 overflow-x-auto">
          {assessments.map(a => (
            <button key={a.id} onClick={() => setSelected(a)}
              className={`px-3.5 py-2 rounded-lg text-xs font-semibold whitespace-nowrap transition-colors shrink-0 ${
                selected?.id === a.id ? "bg-blue-700 text-white shadow-sm" : "text-slate-500 hover:bg-slate-50"}`}>
              {a.name}
            </button>
          ))}
        </div>
      )}

      {reportLoading ? (
        <div className="skeleton h-96 rounded-2xl" />
      ) : error ? (
        <div className="bg-white border border-slate-100 rounded-2xl py-16 text-center" style={{ boxShadow: "var(--shadow-card)" }}>
          <AlertTriangle size={22} className="text-amber-400 mx-auto mb-2" />
          <p className="text-sm text-slate-500">Could not load report.</p>
          <p className="text-xs text-slate-400 mt-1">{error}</p>
        </div>
      ) : report ? (
        <ReportDocument d={report}
          showAll={showAll} setShowAll={setShowAll}
          showAllMat={showAllMat} setShowAllMat={setShowAllMat}
          expandMarket={showAllMarket} />
      ) : null}
    </div>
  );
}
