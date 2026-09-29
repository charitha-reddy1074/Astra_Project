"use client";

import { useState } from "react";
import {
  Building2, Users, BookOpen, ClipboardCheck, Sparkles, BarChart3,
  ChevronRight, Clock, Shield, CheckCircle2, ArrowRight, Info,
  FolderUp, Upload, Eye, Inbox, MapPin,
} from "lucide-react";

// ─── Admin / Central Assessor: full 6-step programme guide ───────────────────

const GUIDES_ADMIN = [
  {
    group: "Setup",
    steps: [
      {
        n: 1, mins: 1, icon: Building2, accent: "brand-tile",
        title: "Scope your programme by market",
        blurb: "Markets are labels — attach Owners and Assessors to them.",
        where: "Users",
        body: [
          "A market is simply a label such as Denmark, France or ASIA, set on a person record in Users. There is no hierarchy tree to maintain — markets are plain strings shared by people and assessments.",
          "Add your Owners and Assessors first and give each one a market. Those labels then drive the coverage view in Users and which assessments an Owner receives document requests for.",
          "When you create an assessment you pick the market it applies to, so a single label flows from person → assessment → report.",
        ],
        tip: "Keep the spelling of a market exact — 'France' and 'france' are treated as different labels, so agree a convention before adding people.",
      },
      {
        n: 2, mins: 2, icon: Users, accent: "brand-tile",
        title: "Add your people and set roles",
        blurb: "Four roles, each with a deliberately different surface.",
        where: "Users",
        body: [
          "Central Admin — full administration: users, framework catalog, and every assessment.",
          "Central Assessor — runs and oversees assessments across markets, but no platform administration.",
          "Assessor — sees and completes only the assessments assigned to them.",
          "Owner — a contributor, not a reviewer. They receive document requests, upload evidence, and use Ask AI. They never see scores or reports.",
        ],
        tip: "Central Admin is the built-in sign-in role and is intentionally not provisionable in the directory — you assign the other three.",
      },
    ],
  },
  {
    group: "Operations",
    steps: [
      {
        n: 3, mins: 3, icon: BookOpen, accent: "brand-tile",
        title: "Load a framework",
        blurb: "Bundled standards, or ingest your own.",
        where: "Knowledge Base › Frameworks",
        body: [
          "Five frameworks ship ready to use: NIST CSF 2.0 (106 subcategories), ISO/IEC 27001:2022 (93 Annex A controls), CIS Controls v8.1.2 (153 safeguards), PCI DSS 4.0, and the Market Assessment maturity rubric.",
          "'Load from Disk' imports the canonical JSON in backend/api/data/canonical/ into the database. 'Upload' ingests a new framework from PDF, DOCX, XLSX, CSV or JSON — the pipeline extracts domains, controls and questions automatically.",
          "Everything is stored as Framework → Domain → Category → Control → Question, so a custom internal standard behaves exactly like a bundled one.",
          "Admins can edit the catalog directly under Knowledge Base › Manage — add, edit, duplicate or move controls, validate a framework, and review the immutable edit history.",
        ],
        tip: "Use Explore on a framework card to browse the full control tree before you scope an assessment against it.",
      },
      {
        n: 4, mins: 2, icon: ClipboardCheck, accent: "brand-tile",
        title: "Create an assessment",
        blurb: "Apply a framework to one market for a period.",
        where: "Assessments › New Assessment",
        body: [
          "Pick the framework, then narrow the scope to the domains you actually intend to assess — scoping to two domains instead of the whole standard is the single biggest lever on how long an engagement takes.",
          "Set the market, assign an Assessor, and optionally upload a custom pre-assessment questionnaire (JSON, CSV, TXT, DOCX or PDF). If you skip it, a default 28-question template is used.",
          "Generating the questionnaire collects the in-scope framework questions plus any pre-assessment questions.",
        ],
        tip: "The assessment starts in Draft. It stays invisible to leadership until it is submitted.",
      },
      {
        n: 5, mins: 5, icon: Sparkles, accent: "brand-tile",
        title: "Conduct it — evidence and AI assistance",
        blurb: "Where most of the work happens.",
        where: "Assessments › open one › Conduct",
        body: [
          "Answers auto-save. The left sidebar tracks progress and lets you filter to Pre-filled, AI follow-up, Answered, or Not answered.",
          "Upload supporting material under Engagement Documents — surveys, interview transcripts, evidence files and policies. Images (PNG/JPG) are read by a vision model and indexed like text.",
          "Pre-fill drafts answers strictly from the uploaded documents and tags each one '[AI pre-filled]' so you can review before accepting.",
          "AI Assistance rates the evidence per category and generates targeted follow-up questions where the evidence is thin.",
          "Raise document requests under Required Documents; the market Owner fulfils them from their own workspace and you accept or reject each one.",
        ],
        tip: "Pre-fill and rating call the Groq API. On the free tier you will hit a daily token cap — scope tightly, or upgrade the key.",
      },
      {
        n: 6, mins: 2, icon: BarChart3, accent: "brand-tile",
        title: "Submit, score and report",
        blurb: "Turn answers into a defensible result.",
        where: "Submit → Score → Reports",
        body: [
          "Submit for Review moves the assessment to In Review. Scores and findings are deliberately hidden until this point, so nothing half-finished reaches leadership.",
          "Scoring applies the weighted model — domain weights, control criticality and evidence quality — and produces an overall percentage, a maturity level, and a finding for every gap.",
          "Reports renders the executive dashboard: maturity tiers, score distribution, domain breakdown and top gaps. Market Analysis rolls every market up for leadership.",
        ],
        tip: "Opening a report re-persists the score, so the assessment list and the report can never drift apart.",
      },
    ],
  },
];

// ─── Assessor: conducting assessments end to end ──────────────────────────────

const GUIDES_ASSESSOR = [
  {
    group: "Running an assessment",
    steps: [
      {
        n: 1, mins: 2, icon: ClipboardCheck, accent: "brand-tile",
        title: "Create or open an assessment",
        blurb: "One framework, one market, one engagement.",
        where: "Assessments › New Assessment",
        body: [
          "Pick a framework, then narrow the scope to only the domains you intend to cover — fewer domains means a faster, more focused engagement.",
          "Assign the market and optionally upload a custom pre-assessment questionnaire (JSON, CSV, TXT, DOCX or PDF). A default 28-question template is used if you skip it.",
          "Assessments already assigned to you appear at the top of your list. Open one to pick up where you left off.",
        ],
        tip: "The assessment stays in Draft — invisible to leadership — until you explicitly submit it.",
      },
      {
        n: 2, mins: 5, icon: Sparkles, accent: "brand-tile",
        title: "Conduct the assessment",
        blurb: "Answer controls, upload evidence, use AI to accelerate.",
        where: "Assessments › open one › Conduct",
        body: [
          "Answers auto-save as you type. The left sidebar tracks progress and lets you filter to Answered, Not answered, AI pre-filled, or AI follow-up.",
          "Upload engagement documents (surveys, transcripts, policy files, images) under Engagement Documents — they are indexed and used by all AI features for this assessment.",
          "Pre-fill generates draft answers from your uploaded documents and tags each '[AI pre-filled]' so you review before accepting. AI Assistance then rates evidence per category and surfaces targeted follow-up questions.",
        ],
        tip: "Pre-fill and AI Assistance call the Groq API. On the free tier you may hit a daily token cap — scope tightly or upgrade the key.",
      },
      {
        n: 3, mins: 3, icon: Inbox, accent: "bg-amber-600",
        title: "Raise document requests",
        blurb: "Ask the market Owner for specific evidence.",
        where: "Assessments › open one › Required Documents",
        body: [
          "Create a document request to ask the market Owner for a specific file — a policy, a scan result, a register. Describe exactly what you need.",
          "The Owner uploads their file from their own workspace. You receive a notification and can accept or reject the submission with a note.",
          "Accepted documents are stored against the assessment and count as evidence. Rejected ones prompt the Owner to re-upload.",
        ],
        tip: "Owners see no scores or findings — they only see the requests you raise and the descriptions you write.",
      },
      {
        n: 4, mins: 2, icon: BarChart3, accent: "brand-tile",
        title: "Submit and score",
        blurb: "Lock the assessment, then apply the weighted model.",
        where: "Assessments › Submit for Review → Score",
        body: [
          "Submit for Review moves the assessment to In Review. No scores or findings are visible to anyone until this point.",
          "Score applies the weighted model: domain weights × control criticality × evidence quality. The result is an overall percentage, a maturity level (L1–L5), and a finding for every gap.",
          "You can re-score after edits — each scoring run overwrites the previous result for that assessment.",
        ],
        tip: "Score first before opening the report — the report page re-persists the score on load, but only if one exists.",
      },
      {
        n: 5, mins: 2, icon: BookOpen, accent: "brand-tile",
        title: "Generate and share the report",
        blurb: "Executive dashboard, per assessment or across all markets.",
        where: "Reports",
        body: [
          "Reports renders the executive dashboard for a single assessment: maturity tiers, score distribution, domain breakdown, and the top gaps ranked by criticality.",
          "Market Analysis compares every scored market side by side for leadership.",
          "Reports can be exported or printed — use your browser's print dialog to save as PDF.",
        ],
        tip: "Market Analysis always compares every market — use the framework filter to focus the comparison.",
      },
    ],
  },
];

// ─── Owner: providing evidence for your market ───────────────────────────────

const GUIDES_OWNER = [
  {
    group: "Providing evidence",
    steps: [
      {
        n: 1, mins: 1, icon: Inbox, accent: "bg-amber-600",
        title: "Find your requests",
        blurb: "Everything the assessor has asked of your market.",
        where: "My Documents",
        body: [
          "My Documents lists every active assessment your market is participating in. Each assessment shows the document requests the assessor has raised.",
          "Requests are sorted by urgency — those needing your action appear first, followed by items you have already provided or that have been accepted.",
          "The status badges tell you at a glance where each request stands: Requested, Provided, Accepted, or Rejected.",
        ],
        tip: "You will only ever see assessments that belong to your market. Scores, findings and reports are never visible from your workspace.",
      },
      {
        n: 2, mins: 2, icon: FolderUp, accent: "bg-orange-600",
        title: "Upload engagement documents",
        blurb: "Supporting material for the whole assessment — not just one request.",
        where: "My Documents › Documents button",
        body: [
          "Click the Documents button on any assessment to upload supporting files — policies, evidence bundles, survey results — that apply to the engagement as a whole.",
          "Supported formats: PDF, DOCX, XLSX, CSV, PNG, JPG. Images are read by a vision model and indexed like text.",
          "These documents are available to the assessor as background evidence and also feed the AI tools for that assessment.",
        ],
        tip: "Upload broad, reusable material here — use individual document requests for specific items the assessor has called out by name.",
      },
      {
        n: 3, mins: 2, icon: Upload, accent: "brand-tile",
        title: "Respond to a specific request",
        blurb: "Upload exactly what the assessor asked for.",
        where: "My Documents › open a request",
        body: [
          "Open a request to read the assessor's description of what they need. The description is specific — it tells you the file type, scope and level of detail expected.",
          "Upload your own file, or generate a Prowler security scan automatically. Prowler scans your cloud environment and produces a structured report in one click.",
          "Prowler reports are generated once and are read-only — what you see is what the assessor receives.",
        ],
        tip: "You always preview exactly what will be sent before it reaches the assessor. There is no silent submission.",
      },
      {
        n: 4, mins: 1, icon: Eye, accent: "brand-tile",
        title: "Track the outcome",
        blurb: "Accepted means done. Rejected means the assessor needs something different.",
        where: "My Documents",
        body: [
          "After you provide a document, the status moves to Provided — the assessor is reviewing it.",
          "Accepted means your submission satisfied the request. No further action needed.",
          "Rejected means the assessor left a note explaining what is missing or wrong. Open the request to read the note and re-upload.",
        ],
        tip: "Statuses update in real time — refresh the page if you are expecting a decision and the badge has not changed.",
      },
      {
        n: 5, mins: 2, icon: Sparkles, accent: "brand-tile",
        title: "Ask the Agent",
        blurb: "Plain-English answers from your uploaded documents.",
        where: "Ask the Agent",
        body: [
          "Ask the Agent searches across all documents uploaded to your market's assessments and answers questions in plain English.",
          "Use it to understand what a control or request is asking for, summarise a policy, or check whether a particular topic is covered in your evidence.",
          "The agent cites the source document and section so you can verify its answer before acting on it.",
        ],
        tip: "The more documents you upload, the better the answers. Thin evidence gives thin answers — the agent will tell you when it cannot find what you are asking about.",
      },
    ],
  },
];

// ─── Status reference for owners ─────────────────────────────────────────────

const STATUS_LADDER = [
  { label: "Requested",  tone: "bg-amber-50 text-amber-700 border-amber-200",   desc: "The assessor has asked for this document. Action required — open the request and upload." },
  { label: "Provided",   tone: "bg-blue-50 text-blue-700 border-blue-200",      desc: "You have uploaded a file. The assessor is reviewing it — no action needed right now." },
  { label: "Accepted",   tone: "bg-emerald-50 text-emerald-700 border-emerald-200", desc: "The assessor is satisfied with your submission. This request is closed." },
  { label: "Rejected",   tone: "bg-rose-50 text-rose-600 border-rose-200",      desc: "The assessor needs something different. Open the request to read their note and re-upload." },
];

// ─── Scoring reference for admins / assessors ─────────────────────────────────

const LADDER = [
  { label: "Mature",       tone: "bg-emerald-50 text-emerald-700 border-emerald-200", desc: "Implemented, owned, evidenced and reviewed on a defined cadence." },
  { label: "Partial",      tone: "bg-amber-50 text-amber-700 border-amber-200",       desc: "Exists but is informal, inconsistently applied, or incomplete in coverage." },
  { label: "Critical Gap", tone: "bg-rose-50 text-rose-600 border-rose-200",          desc: "No defined owner, tooling or repeatable process. Prioritise for remediation." },
];

// ─── Shared UI ────────────────────────────────────────────────────────────────

function StepCard({ step, open, onToggle }) {
  const Icon = step.icon;
  return (
    <div className={`bg-white rounded-2xl border transition-all ${open ? "border-blue-200 shadow-sm" : "border-slate-100 hover:border-slate-200"}`}>
      <button onClick={onToggle} className="w-full flex items-start gap-4 p-5 text-left">
        <div className={`shrink-0 w-10 h-10 rounded-xl ${step.accent || "brand-tile"} text-white flex items-center justify-center`}>
          <Icon size={18} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Step {step.n}</span>
            <span className="text-slate-300">·</span>
            <span className="inline-flex items-center gap-1 text-[10px] text-slate-400">
              <Clock size={10} /> {step.mins} min read
            </span>
          </div>
          <h3 className="text-sm font-bold text-slate-900">{step.title}</h3>
          <p className="text-xs text-slate-500 mt-0.5">{step.blurb}</p>
          <span className="inline-flex items-center gap-1 mt-2 text-[10px] font-semibold text-blue-600">
            {step.where} <ArrowRight size={10} />
          </span>
        </div>
        <ChevronRight size={16} className={`shrink-0 mt-1 text-slate-300 transition-transform ${open ? "rotate-90" : ""}`} />
      </button>
      {open && (
        <div className="px-5 pb-5 pl-19 space-y-2.5">
          <div className="ml-14 space-y-2.5">
            {step.body.map((p, i) => (
              <p key={i} className="text-xs text-slate-600 leading-relaxed flex gap-2">
                <CheckCircle2 size={13} className="shrink-0 mt-0.5 text-emerald-500" />
                <span>{p}</span>
              </p>
            ))}
            {step.tip && (
              <div className="flex gap-2 items-start bg-blue-50 border border-blue-100 rounded-xl px-3 py-2.5 mt-3">
                <Info size={13} className="shrink-0 mt-0.5 text-blue-600" />
                <p className="text-xs text-blue-800 leading-relaxed">{step.tip}</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Role-specific configs ────────────────────────────────────────────────────

const ROLE_CONFIG = {
  owner: {
    guides: GUIDES_OWNER,
    subtitle: "How to provide evidence, respond to assessor requests, and use Ask the Agent — step by step.",
    reference: {
      icon: MapPin,
      title: "What the statuses mean",
      desc: "Every document request moves through four states. Here is what each one means and what action (if any) you need to take.",
      ladder: STATUS_LADDER,
    },
  },
  assessor: {
    guides: GUIDES_ASSESSOR,
    subtitle: "How to run an assessment from creation to report — evidence, AI tools, document requests and scoring.",
    reference: {
      icon: Shield,
      title: "How scoring works",
      desc: "Every answered control lands on a three-rung ladder. Controls are weighted by domain importance and criticality, so a gap in a critical control moves the number more than a gap in an informational one. The overall percentage maps to a maturity level from L1 to L5.",
      ladder: LADDER,
    },
  },
};

// Central assessor sees the same guide as assessor
ROLE_CONFIG.central_assessor = ROLE_CONFIG.assessor;
ROLE_CONFIG.central_admin = {
  guides: GUIDES_ADMIN,
  subtitle: "Short, practical guides to running the assessment programme end to end — from modelling your markets to shipping a leadership report.",
  reference: {
    icon: Shield,
    title: "How scoring works",
    desc: "Every answered control lands on a three-rung ladder. Controls are weighted by domain importance and criticality, so a gap in a critical control moves the number more than a gap in an informational one. The overall percentage maps to a maturity level from L1 to L5.",
    ladder: LADDER,
  },
};

// ─── Main component ───────────────────────────────────────────────────────────

export default function HelpView({ role }) {
  const config = ROLE_CONFIG[role] ?? ROLE_CONFIG.central_admin;
  const { guides, subtitle, reference } = config;
  const total = guides.reduce((n, g) => n + g.steps.length, 0);
  const [open, setOpen] = useState(1);
  const RefIcon = reference.icon;

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Getting started</h1>
        <p className="text-sm text-slate-500 mt-1">
          {subtitle.replace("{total}", total)}
        </p>
      </div>

      {guides.map((g) => (
        <div key={g.group} className="space-y-3">
          <h2 className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">{g.group}</h2>
          {g.steps.map((s) => (
            <StepCard key={s.n} step={s} open={open === s.n}
                      onToggle={() => setOpen(open === s.n ? null : s.n)} />
          ))}
        </div>
      ))}

      <div className="space-y-3">
        <h2 className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Reference</h2>
        <div className="bg-white rounded-2xl border border-slate-100 p-5">
          <div className="flex items-center gap-2 mb-3">
            <RefIcon size={15} className="text-violet-600" />
            <h3 className="text-sm font-bold text-slate-900">{reference.title}</h3>
          </div>
          <p className="text-xs text-slate-600 leading-relaxed mb-4">{reference.desc}</p>
          <div className="space-y-2">
            {reference.ladder.map((l) => (
              <div key={l.label} className="flex items-start gap-3">
                <span className={`shrink-0 text-[10px] font-bold px-2 py-1 rounded-lg border ${l.tone}`}>
                  {l.label}
                </span>
                <p className="text-xs text-slate-600 leading-relaxed pt-0.5">{l.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
