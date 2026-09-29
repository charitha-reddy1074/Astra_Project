"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import {
  Inbox, Library, Sparkles, CheckCircle2, MapPin, Clock, AlertCircle, FolderUp,
  Compass, Upload, Eye, X,
} from "lucide-react";
import { api } from "@/lib/api";
import { getIdentity } from "@/lib/auth";
import { getUsers, organizationLabelOf, getOrganizationList } from "@/lib/users";
import OwnerRequestCard, { NEEDS_ACTION } from "./OwnerRequestCard";
import OwnerAssessmentView from "./OwnerAssessmentView";

// ─────────────────────────────────────────────────────────────────────────────
// The contributor's workspace.
//
// A Team Member or Evidence Contributor is a CONTRIBUTOR, not a reviewer: their
// whole job is to receive document requests from a reviewer, understand what is
// being asked for, and upload the evidence. So this screen leads with the
// requests themselves — not with a list of assessments — and shows no scores,
// findings or reports.
//
// Visibility is enforced SERVER-SIDE: GET /assessments and GET /document-requests
// are scoped by AssessmentService.list_for_caller / list_document_requests_all to
// the assessments this contributor is actually involved in. Nothing here
// re-filters a wider list client-side; if the backend returns it, the
// contributor is entitled to it.
// ─────────────────────────────────────────────────────────────────────────────

function StatTile({ icon: Icon, label, value, tone }) {
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-slate-100 bg-white px-4 py-3.5"
      style={{ boxShadow: "var(--shadow-card)" }}>
      <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${tone}`}>
        <Icon size={15} />
      </span>
      <div className="min-w-0">
        <p className="text-lg font-bold leading-none tabular-nums text-slate-900">{value}</p>
        <p className="mt-1 truncate text-[11px] font-medium text-slate-400">{label}</p>
      </div>
    </div>
  );
}

const GS_STEPS = [
  { icon: FolderUp, title: "Open an assessment",
    body: "Pick an assessment below and click Documents to submit engagement documents — pre-assessment, policies and evidence — scoped to that assessment only." },
  { icon: Upload, title: "Respond to a request",
    body: "For each request, upload your own file — or generate a report automatically with the Prowler tool." },
  { icon: Eye, title: "Preview before you submit",
    body: "You always see exactly what will be sent to the reviewer first. Prowler reports are generated once and are read-only." },
  { icon: CheckCircle2, title: "Track the review",
    body: "The reviewer accepts your evidence or asks for a re-upload. Statuses update right here on this page." },
];

const GS_KEY = "cyberai_contributor_gs_dismissed";

function GettingStarted() {
  // Lazy, SSR-safe read of the dismiss flag (this authed view is client-only).
  const [hidden, setHidden] = useState(() => {
    if (typeof window === "undefined") return false;
    try { return localStorage.getItem(GS_KEY) === "1"; } catch { return false; }
  });
  const dismiss = () => {
    try { localStorage.setItem(GS_KEY, "1"); } catch {}
    setHidden(true);
  };
  if (hidden) return null;

  return (
    <div className="relative overflow-hidden rounded-2xl border p-5"
      style={{ boxShadow: "var(--shadow-card)", borderColor: "var(--accent-soft-line)", background: "var(--brand-wash-duo)" }}>
      <button onClick={dismiss} title="Dismiss"
        className="absolute right-3 top-3 rounded-lg p-1 text-slate-300 transition-colors hover:bg-white/60 hover:text-slate-600">
        <X size={15} />
      </button>
      <div className="flex items-center gap-2.5">
        <span className="brand-tile flex h-9 w-9 items-center justify-center rounded-xl">
          <Compass size={16} />
        </span>
        <div>
          <h3 className="text-sm font-bold text-slate-900">Getting started</h3>
          <p className="text-[11px] text-slate-500">How to provide evidence for your organization, step by step.</p>
        </div>
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {GS_STEPS.map((s, i) => {
          const Icon = s.icon;
          return (
            <div key={i} className="flex gap-3 rounded-xl border border-amber-100/70 bg-white/70 p-3">
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-amber-50 text-amber-600">
                <Icon size={14} />
              </span>
              <div className="min-w-0">
                <p className="text-xs font-bold text-slate-800">
                  <span className="mr-1 text-amber-600">{i + 1}.</span>{s.title}
                </p>
                <p className="mt-0.5 text-[11px] leading-relaxed text-slate-500">{s.body}</p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function OwnerDashboard({ role, setActiveView }) {
  const [requests,    setRequests]    = useState([]);
  const [assessments, setAssessments] = useState([]);
  const [loading,     setLoading]     = useState(true);
  const [error,       setError]       = useState(null);
  const [uploadFor,   setUploadFor]   = useState(null);

  // The contributor's organization, purely for the "you are working on behalf of" badge.
  const identity        = getIdentity();
  const orgList         = getOrganizationList();
  const contributorUser = getUsers().find(u => u.email === identity);
  const organization    = organizationLabelOf(contributorUser?.organization, orgList);

  const load = useCallback(async () => {
    try {
      const [reqs, asmts] = await Promise.all([
        api.documentRequests.listAll(),
        api.assessments.list(),
      ]);
      setRequests(reqs || []);
      setAssessments(asmts || []);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const assessmentById = useMemo(
    () => Object.fromEntries(assessments.map(a => [a.id, a])), [assessments],
  );

  // Group the requests by assessment, action-needed engagements first.
  const groups = useMemo(() => {
    const map = new Map();
    for (const r of requests) {
      if (!map.has(r.assessment_id)) {
        const a = assessmentById[r.assessment_id];
        map.set(r.assessment_id, {
          id: r.assessment_id,
          name: r.assessment_name || a?.name || "Assessment",
          organization: r.organization || a?.organization || r.market_label || null,
          requests: [],
        });
      }
      map.get(r.assessment_id).requests.push(r);
    }
    const rank = { rejected: 0, requested: 1, provided: 2, accepted: 3 };
    const out = [...map.values()];
    for (const g of out) {
      g.requests.sort((x, y) => (rank[x.status] ?? 9) - (rank[y.status] ?? 9));
      g.outstanding = g.requests.filter(r => NEEDS_ACTION.has(r.status)).length;
    }
    return out.sort((a, b) => b.outstanding - a.outstanding || a.name.localeCompare(b.name));
  }, [requests, assessmentById]);

  const counts = {
    outstanding: requests.filter(r => NEEDS_ACTION.has(r.status)).length,
    awaiting:    requests.filter(r => r.status === "provided").length,
    accepted:    requests.filter(r => r.status === "accepted").length,
  };

  // "Upload documents" opens the existing contributor document surface for one
  // engagement (requested + free-form engagement uploads).
  if (uploadFor) {
    const a = assessmentById[uploadFor.id] || { id: uploadFor.id, name: uploadFor.name };
    return (
      <div style={{ height: "calc(100vh - 3.5rem)" }}>
        <OwnerAssessmentView
          assessment={a}
          onBack={() => { setUploadFor(null); load(); }}
        />
      </div>
    );
  }

  return (
    <div className="fade-in-up mx-auto max-w-4xl space-y-6 p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-slate-900">My Documents</h2>
          <p className="mt-1 text-sm text-slate-500">
            Evidence the reviewers have asked you for. Open a request to see what will satisfy it.
          </p>
        </div>
        {organization && (
          <span className="flex shrink-0 items-center gap-1.5 rounded-xl border border-amber-100 bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-700">
            <MapPin size={12} className="shrink-0" /> {organization}
          </span>
        )}
      </div>

      <GettingStarted />

      {error && (
        <div className="rounded-2xl border border-rose-100 bg-rose-50 px-4 py-3 text-xs font-medium text-rose-700">
          {error}
        </div>
      )}

      {!loading && requests.length > 0 && (
        <div className="grid grid-cols-3 gap-4">
          <StatTile icon={AlertCircle} label="Need your action" value={counts.outstanding}
            tone="bg-amber-50 text-amber-600" />
          <StatTile icon={Clock} label="Awaiting reviewer review" value={counts.awaiting}
            tone="bg-blue-50 text-blue-600" />
          <StatTile icon={CheckCircle2} label="Accepted" value={counts.accepted}
            tone="bg-emerald-50 text-emerald-600" />
        </div>
      )}

      {loading && (
        <div className="space-y-4">
          {[1, 2, 3].map(i => <div key={i} className="skeleton h-32 rounded-2xl" />)}
        </div>
      )}

      {!loading && !error && requests.length === 0 && (
        <div className="rounded-2xl border border-slate-100 bg-white py-16 text-center"
          style={{ boxShadow: "var(--shadow-card)" }}>
          <Inbox size={28} className="mx-auto mb-4 text-slate-200" />
          <p className="mb-1 text-sm font-semibold text-slate-500">Nothing has been requested from you yet.</p>
          <p className="text-xs text-slate-400">
            When a reviewer asks {organization || "your organization"} for
            evidence, the request will appear here with guidance on what to provide.
          </p>
        </div>
      )}

      {!loading && groups.map(g => (
        <section key={g.id} className="space-y-3">
          <div className="flex items-baseline justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-sm font-bold text-slate-900">{g.name}</p>
              {g.organization && <p className="text-[11px] font-semibold text-blue-600">{g.organization}</p>}
            </div>
            <div className="flex shrink-0 items-center gap-3">
              <span className="text-[11px] font-medium text-slate-400">
                {g.outstanding > 0
                  ? `${g.outstanding} of ${g.requests.length} need action`
                  : `${g.requests.length} request${g.requests.length === 1 ? "" : "s"} — all handled`}
              </span>
              <button onClick={() => setUploadFor(g)}
                className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1.5 text-[11px] font-semibold text-slate-600 transition-all hover:border-blue-300 hover:text-blue-700">
                <FolderUp size={11} /> Documents
              </button>
            </div>
          </div>
          <div className="space-y-3">
            {g.requests.map(r => (
              <OwnerRequestCard key={r.id} request={r} onProvided={load} />
            ))}
          </div>
        </section>
      ))}

      <div className="flex gap-3 pt-2">
        <button onClick={() => setActiveView("policy")}
          className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-xs font-semibold text-slate-700 transition-all hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700"
          style={{ boxShadow: "var(--shadow-card)" }}>
          <Library size={13} /> Policy Library
        </button>
        <button onClick={() => setActiveView("ai")}
          className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-xs font-semibold text-slate-700 transition-all hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700"
          style={{ boxShadow: "var(--shadow-card)" }}>
          <Sparkles size={13} /> Ask the Agent
        </button>
      </div>
    </div>
  );
}
