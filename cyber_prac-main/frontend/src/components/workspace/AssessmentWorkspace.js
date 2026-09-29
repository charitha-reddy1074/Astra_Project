"use client";

import { useEffect, useState, useMemo, useCallback } from "react";
import { ChevronLeft, ChevronDown, ChevronRight, Award, Send, Sparkles, Wand2, FolderOpen } from "lucide-react";
import { api } from "@/lib/api";
import { canDo, isContributor as isContributorRole } from "@/lib/auth";
import QuestionInput from "./QuestionInput";
import ComplianceTab from "./ComplianceTab";
import { EngagementDocumentsTab, AiAssistanceTab, RequiredDocumentsTab } from "./WorkspaceTabContent";

const STATUS_BADGE = { draft: "badge-neutral", in_progress: "badge-medium", in_review: "badge-medium", completed: "badge-success" };
const STATUS_LABEL = { draft: "Draft", in_progress: "In Progress", in_review: "In Review", completed: "Completed" };
const ALL_TABS = [
  { key: "conduct",    label: "Conduct" },
  { key: "documents",  label: "Required Documents" },
  { key: "engagement", label: "Engagement Documents" },
  { key: "ai",         label: "AI Assistance" },
  { key: "compliance", label: "Compliance Report" },
];

// Contributors only see document-related tabs
const CONTRIBUTOR_TABS = [
  { key: "documents",  label: "Requested Documents" },
  { key: "engagement", label: "Upload Documents" },
];

export default function AssessmentWorkspace({ assessment, role, onBack, onComplete }) {
  const isContributor = isContributorRole(role);
  const TABS = isContributor ? CONTRIBUTOR_TABS : ALL_TABS;
  const initialTab = isContributor ? "documents" : "conduct";

  const [questions,   setQuestions]   = useState([]);
  const [responses,   setResponses]   = useState({});
  const [responseIds, setResponseIds] = useState({});
  const [notes,       setNotes]       = useState({});
  const [activeDomain, setActiveDomain] = useState(null);
  const [loading,     setLoading]     = useState(true);
  const [saving,      setSaving]      = useState(false);
  const [scoring,     setScoring]     = useState(false);
  const [prefilling,  setPrefilling]  = useState(false);
  const [submitting,  setSubmitting]  = useState(false);
  const [localStatus, setLocalStatus] = useState(assessment.status);
  const [scoreResult, setScoreResult] = useState(null);
  const [findings,    setFindings]    = useState([]);
  const [activeTab,   setActiveTab]   = useState(initialTab);
  const [preAssessmentFileName, setPreAssessmentFileName] = useState(assessment.pre_assessment_file_name || null);
  const [filterMode,      setFilterMode]      = useState(null); // null | "prefilled" | "ai_followup" | "answered" | "unanswered"
  const [expandedDomains, setExpandedDomains] = useState(() => new Set());

  const canAnswer  = canDo(role, "answerQuestions");
  const canUpload  = canDo(role, "uploadEvidence");
  const canComment = canDo(role, "comment");
  const canSubmit  = canDo(role, "submitAssessment");
  const canScore   = canDo(role, "scoreAssessment");

  // Build the in-scope question list (framework → domain → category → control →
  // question) plus any AI-generated follow-up questions appended per category.
  const buildQuestions = useCallback((frameworks, asmtData) => {
    const selectedDomains = new Set(asmtData.selected_domains || assessment.selected_domains || []);
    const qs = [];
    for (const r of frameworks) {
      for (const domain of r.domains || []) {
        if (selectedDomains.size && !(
          selectedDomains.has(`${r.id}:${domain.code}`) ||
          selectedDomains.has(domain.code) || selectedDomains.has(domain.id)
        )) continue;
        const cats = (domain.categories && domain.categories.length)
          ? domain.categories
          : [{ code: domain.code, name: null, criteria_statement: null, controls: domain.controls || [] }];
        for (const cat of cats) {
          for (const ctrl of cat.controls || []) {
            for (const q of ctrl.questions || []) {
              qs.push({
                id: q.id, question: q.text, question_type: q.question_type, choices: q.choices,
                control_id: ctrl.code, domain: domain.code, domain_name: domain.name,
                category_code: cat.code, category_name: cat.name,
                criteria_statement: cat.criteria_statement, statement: ctrl.statement, ai: false,
              });
            }
          }
        }
      }
    }
    // Append per-assessment follow-up questions.
    for (const f of (asmtData.followups || [])) {
      const source = f.source || "ai";
      const isAi = source !== "pre_assessment";
      qs.push({
        id: f.id, question: f.text, question_type: f.question_type || "FREE_TEXT", choices: null,
        control_id: f.control_code, domain: f.domain_code, domain_name: f.domain_name,
        category_code: f.category_code, category_name: f.category_name,
        criteria_statement: null, statement: null, ai: isAi,
      });
    }
    return qs;
  }, [assessment.selected_domains]);

  const loadData = useCallback(async () => {
    const asmtData = await api.assessments.get(assessment.id);
    const fwResults = await Promise.allSettled(
      (asmtData.framework_ids || assessment.framework_ids || []).map((id) => api.frameworks.get(id))
    );
    const frameworks = fwResults.filter((r) => r.status === "fulfilled").map((r) => r.value);
    setQuestions(buildQuestions(frameworks, asmtData));

    const responseList = asmtData.responses || [];
    const rMap = {}, rIdMap = {}, nMap = {};
    for (const r of responseList) {
      if (!r.question_id) continue;
      rMap[r.question_id]  = r.response_value;
      rIdMap[r.question_id] = r.id;
      if (r.notes) nMap[r.question_id] = r.notes;
    }
    setResponses(rMap); setResponseIds(rIdMap); setNotes(nMap);
    if (asmtData.pre_assessment_file_name) setPreAssessmentFileName(asmtData.pre_assessment_file_name);
    const submitted = asmtData.status === "in_review" || asmtData.status === "completed";
    if (submitted && asmtData.overall_score != null) {
      setScoreResult({ overall_score: asmtData.overall_score, maturity_level: asmtData.maturity_level,
        domain_scores: (asmtData.scores || []).filter((s) => s.level === "domain") });
      if (asmtData.findings?.length) setFindings(asmtData.findings);
    }
    setLocalStatus(asmtData.status);
  }, [assessment.id, assessment.framework_ids, buildQuestions]);

  useEffect(() => {
    (async () => {
      const asmtData = await api.assessments.get(assessment.id);
      const hasPreAssessment = (asmtData.followups || []).some((f) => (f.source || "ai") === "pre_assessment");
      const fwIds = asmtData.framework_ids || assessment.framework_ids || [];
      if (asmtData.status === "draft" && (fwIds.length > 0 || hasPreAssessment)) {
        try { await api.assessments.generateQuestionnaire(assessment.id); } catch {}
      }
      await loadData();
    })().catch(console.error).finally(() => setLoading(false));
  }, [assessment.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Domain → ordered categories → question indices.
  const domainGroups = useMemo(() => {
    const domains = {};
    const order = [];
    questions.forEach((q, i) => {
      const dk = q.domain || "Other";
      if (!domains[dk]) { domains[dk] = { code: dk, name: q.domain_name || dk, cats: {}, catOrder: [] }; order.push(dk); }
      const ck = q.category_code || dk;
      if (!domains[dk].cats[ck]) { domains[dk].cats[ck] = { code: ck, name: q.category_name, criteria: q.criteria_statement, indices: [] }; domains[dk].catOrder.push(ck); }
      domains[dk].cats[ck].indices.push(i);
    });
    return order.map((dk) => ({
      ...domains[dk],
      indices: domains[dk].catOrder.flatMap((ck) => domains[dk].cats[ck].indices),
      categories: domains[dk].catOrder.map((ck) => domains[dk].cats[ck]),
    }));
  }, [questions]);

  // Control-level breakdown for sidebar navigation.
  const domainControlGroups = useMemo(() => domainGroups.map(group => {
    const controlMap = {};
    const controlOrder = [];
    group.indices.forEach(i => {
      const q = questions[i];
      if (!q) return;
      const ctrl = q.control_id || "—";
      if (!controlMap[ctrl]) {
        controlMap[ctrl] = { control_id: ctrl, firstIndex: i, indices: [] };
        controlOrder.push(ctrl);
      }
      controlMap[ctrl].indices.push(i);
    });
    return { ...group, controls: controlOrder.map(c => controlMap[c]) };
  }), [domainGroups, questions]);

  const scrollToDomain = useCallback((code) => {
    setActiveDomain(code);
    if (typeof document !== "undefined")
      document.getElementById(`dom-${code}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const scrollToQuestion = useCallback((index) => {
    if (typeof document !== "undefined")
      document.getElementById(`q-${index}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const toggleDomain = useCallback((code) => {
    setExpandedDomains(prev => {
      const next = new Set(prev);
      next.has(code) ? next.delete(code) : next.add(code);
      return next;
    });
  }, []);

  const answeredCount = questions.filter((q) => responses[q.id] != null && responses[q.id] !== "").length;
  const totalPct      = questions.length ? Math.round((answeredCount / questions.length) * 100) : 0;

  const prefilledIds = useMemo(
    () => new Set(questions.filter((q) => notes[q.id]?.startsWith("[AI pre-filled]")).map((q) => q.id)),
    [questions, notes],
  );
  const prefilledCount   = prefilledIds.size;
  const answeredManual   = answeredCount - prefilledCount;
  const unansweredCount  = questions.length - answeredCount;
  const aiFollowupCount  = questions.filter((q) => q.ai).length;

  const toggleFilter = (mode) => setFilterMode((prev) => (prev === mode ? null : mode));

  const questionVisible = useCallback((q) => {
    if (!filterMode) return true;
    const answered = responses[q.id] != null && responses[q.id] !== "";
    if (filterMode === "prefilled")    return prefilledIds.has(q.id);
    if (filterMode === "answered")     return answered && !prefilledIds.has(q.id);
    if (filterMode === "unanswered")   return !answered;
    if (filterMode === "ai_followup")  return q.ai === true;
    return true;
  }, [filterMode, prefilledIds, responses]);

  const saveAnswer = useCallback(async (q, value) => {
    if (!canAnswer || !q) return;
    setResponses((p) => ({ ...p, [q.id]: value }));
    setSaving(true);
    try {
      const payload = { question_id: q.id, control_id: q.control_id, response_value: value, notes: notes[q.id] || null };
      const saved = await api.assessments.saveResponse(assessment.id, payload);
      setResponseIds((p) => ({ ...p, [q.id]: saved.id }));
    } catch (e) { console.error(e); } finally { setSaving(false); }
  }, [notes, assessment.id, canAnswer]);

  const saveNote = useCallback(async (q) => {
    if (!canComment || !q || !notes[q.id]) return;
    try {
      const payload = { question_id: q.id, control_id: q.control_id, response_value: responses[q.id] || null, notes: notes[q.id] };
      const saved = await api.assessments.saveResponse(assessment.id, payload);
      setResponseIds((p) => ({ ...p, [q.id]: saved.id }));
    } catch {}
  }, [notes, responses, assessment.id, canComment]);

  const handlePrefill = async () => {
    setPrefilling(true);
    try {
      const res = await api.assessments.prefill(assessment.id);
      if (res.ok === false) { alert(res.error || "Pre-fill is unavailable."); return; }
      await loadData();
      const s = res.summary || {};
      alert(`Pre-filled ${s.filled || 0} of ${s.total || 0} answers from ${(s.evidence_files || []).length} document(s).` +
            (s.message ? `\n${s.message}` : ""));
    } catch (e) { alert(e.message); } finally { setPrefilling(false); }
  };

  const handleScore = async () => {
    setScoring(true);
    try {
      const result = await api.assessments.score(assessment.id);
      setScoreResult(result);
      setLocalStatus("completed");
      const fs = await api.assessments.findings(assessment.id).catch(() => []);
      setFindings(fs);
      onComplete?.();
      alert(`Scored: ${result.overall_score}% (maturity level ${result.maturity_level}/5). See the Reports section for the full report.`);
    } catch (e) { alert(e.message); } finally { setScoring(false); }
  };

  const handleSubmit = async () => {
    if (!window.confirm("Submit this assessment for review? You can still update answers until it is scored.")) return;
    setSubmitting(true);
    try {
      await api.assessments.submit(assessment.id);
      setLocalStatus("in_review");
      onComplete?.();
      alert("Assessment submitted for review.");
    } catch (e) { alert(e.message); } finally { setSubmitting(false); }
  };

  const showScore  = canScore && localStatus === "in_review";
  const showSubmit = canSubmit && localStatus !== "in_review"
                     && localStatus !== "completed" && answeredCount > 0;

  if (loading) return (
    <div className="h-full flex items-center justify-center">
      <div className="text-center">
        <div className="space-y-2 w-40 mx-auto mb-3">
          {[1,2,3].map(i => <div key={i} className="skeleton h-2.5 rounded" style={{ width: `${100 - i*15}%`, margin: "0 auto" }} />)}
        </div>
        <p className="text-xs text-slate-400">Loading assessment…</p>
      </div>
    </div>
  );

  return (
    <div className="flex flex-col h-full bg-slate-50">

      {/* Sticky header */}
      <div className="shrink-0 bg-white border-b border-slate-100 px-4 py-2.5 flex items-center gap-3">
        <button onClick={onBack}
          className="flex items-center gap-1 text-xs font-medium text-slate-500 hover:text-blue-600 transition-colors shrink-0">
          <ChevronLeft size={13} /> All
        </button>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-bold text-slate-900 truncate">{assessment.name}</p>
          {assessment.organization && <p className="text-[11px] text-slate-400 truncate">{assessment.organization}</p>}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className={`badge ${STATUS_BADGE[localStatus] || "badge-neutral"}`}>
            {STATUS_LABEL[localStatus] || localStatus}
          </span>
          {scoreResult && <span className="text-sm font-bold text-slate-900 tabular-nums">{scoreResult.overall_score}%</span>}
          {showScore && (
            <button onClick={handleScore} disabled={scoring} className="btn-primary text-xs px-3 py-1.5">
              <Award size={12} /> {scoring ? "…" : "Score"}
            </button>
          )}
          {showSubmit && (
            <button onClick={handleSubmit} disabled={submitting} className="btn-primary text-xs px-3 py-1.5">
              <Send size={12} /> {submitting ? "Submitting…" : "Submit"}
            </button>
          )}
        </div>
      </div>

      {/* Tab bar */}
      <div className="shrink-0 flex bg-white border-b border-slate-100 px-4 overflow-x-auto">
        {TABS.map(t => (
          <button key={t.key} onClick={() => setActiveTab(t.key)}
            className={`px-4 py-2.5 text-xs font-semibold border-b-2 whitespace-nowrap transition-colors ${
              activeTab === t.key ? "border-blue-600 text-blue-700" : "border-transparent text-slate-400 hover:text-slate-700"}`}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {activeTab === "conduct" ? (
          questions.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center gap-3 text-center p-6">
              <p className="text-sm text-slate-500">No questions found for this assessment.</p>
            </div>
          ) : (
          <div className="flex h-full">

            {/* Combined sidebar: progress + filters + domain→control nav */}
            <div className="w-64 shrink-0 border-r border-slate-100 bg-white overflow-y-auto flex flex-col" style={{ boxShadow: "1px 0 0 0 var(--border-hairline)" }}>

              {/* Progress */}
              <div className="px-4 pt-4 pb-3 border-b border-slate-100">
                <p className="text-[9px] font-bold text-slate-400 tracking-widest uppercase mb-3">Progress</p>
                <div className="flex items-center gap-3">
                  <div className="relative w-12 h-12 shrink-0 rounded-full flex items-center justify-center"
                    style={{ background: `conic-gradient(var(--brand-gradient) ${totalPct * 3.6}deg, var(--chart-track) ${totalPct * 3.6}deg)` }}>
                    <div className="absolute inset-1.5 rounded-full bg-white" />
                    <span className="relative z-10 text-[10px] font-bold text-slate-900">{totalPct}%</span>
                  </div>
                  <div>
                    <p className="text-sm font-bold text-slate-900 tabular-nums leading-tight">
                      {answeredCount}<span className="text-slate-400 font-normal text-xs"> / {questions.length}</span>
                    </p>
                    <p className="text-[10px] text-slate-400 mt-0.5">questions answered</p>
                  </div>
                </div>
              </div>

              {/* Filters */}
              <div className="px-4 py-3 border-b border-slate-100">
                <p className="text-[9px] font-bold text-slate-400 tracking-widest uppercase mb-2">Filters</p>
                <div className="space-y-1">
                  <button onClick={() => toggleFilter("prefilled")}
                    className={`w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-[11px] font-medium border transition-colors
                      ${filterMode === "prefilled" ? "chip-selected" : "bg-white border-slate-200 hover:bg-slate-50 text-slate-600"}`}>
                    <span className="flex items-center gap-1.5"><Sparkles size={10} />Pre-filled</span>
                    <span className="font-bold tabular-nums">{prefilledCount}</span>
                  </button>
                  <button onClick={() => toggleFilter("ai_followup")}
                    className={`w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-[11px] font-medium border transition-colors
                      ${filterMode === "ai_followup" ? "chip-selected" : "bg-white border-slate-200 hover:bg-slate-50 text-slate-600"}`}>
                    <span className="flex items-center gap-1.5"><Sparkles size={10} />AI follow-up</span>
                    <span className="font-bold tabular-nums">{aiFollowupCount}</span>
                  </button>
                  <button onClick={() => toggleFilter("answered")}
                    className={`w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-[11px] font-medium border transition-colors
                      ${filterMode === "answered" ? "chip-selected" : "bg-white border-slate-200 hover:bg-slate-50 text-slate-600"}`}>
                    <span>Answered</span>
                    <span className="font-bold tabular-nums">{answeredManual}</span>
                  </button>
                  <button onClick={() => toggleFilter("unanswered")}
                    className={`w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-[11px] font-medium border transition-colors
                      ${filterMode === "unanswered" ? "chip-selected" : "bg-white border-slate-200 hover:bg-slate-50 text-slate-600"}`}>
                    <span>Not answered</span>
                    <span className="font-bold tabular-nums">{unansweredCount}</span>
                  </button>
                </div>
                {filterMode && (
                  <button onClick={() => setFilterMode(null)}
                    className="w-full text-center text-[10px] text-slate-400 hover:text-slate-700 transition-colors mt-1.5">
                    × Clear filter
                  </button>
                )}
              </div>

              {/* Domain → control navigation */}
              <div className="flex-1 overflow-y-auto">
                <div className="px-4 pt-3 pb-1">
                  <p className="text-[9px] font-bold text-slate-400 tracking-widest uppercase">Domains</p>
                </div>
                <nav className="px-2 pb-2 space-y-0.5">
                  {domainControlGroups.map((group) => {
                    const answered  = group.indices.filter((i) => responses[questions[i]?.id]).length;
                    const pct       = Math.round((answered / group.indices.length) * 100);
                    const isActive  = activeDomain === group.code;
                    const isExpanded = expandedDomains.has(group.code);

                    return (
                      <div key={group.code}>
                        <div className={`rounded-lg transition-colors ${isActive ? "bg-blue-50" : "hover:bg-slate-50"}`}>
                          <div className="flex items-center">
                            <button onClick={() => scrollToDomain(group.code)} className="flex-1 text-left px-2.5 py-2 min-w-0">
                              <div className="flex items-center justify-between mb-1">
                                <span className={`text-[11px] font-bold truncate ${isActive ? "text-blue-700" : "text-slate-600"}`}>{group.code}</span>
                                <span className="text-[10px] text-slate-400 tabular-nums ml-1 shrink-0">{answered}/{group.indices.length}</span>
                              </div>
                              <div className="h-1 bg-slate-100 rounded-full overflow-hidden">
                                <div className={`h-full rounded-full transition-all ${pct >= 60 ? "bg-emerald-400" : pct > 0 ? "bg-blue-400" : ""}`} style={{ width: `${pct}%` }} />
                              </div>
                            </button>
                            <button onClick={() => toggleDomain(group.code)}
                              className={`p-1.5 rounded-md mr-1 transition-colors ${isExpanded ? "text-blue-500" : "text-slate-300 hover:text-slate-500"}`}>
                              {isExpanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                            </button>
                          </div>
                        </div>

                        {isExpanded && (
                          <div className="ml-3 mt-0.5 mb-1 space-y-0.5">
                            {group.controls.map((ctrl) => {
                              const ctrlAnswered = ctrl.indices.filter((i) => responses[questions[i]?.id]).length;
                              const ctrlTotal    = ctrl.indices.length;
                              const complete     = ctrlAnswered === ctrlTotal && ctrlTotal > 0;
                              const partial      = ctrlAnswered > 0 && !complete;
                              return (
                                <button key={ctrl.control_id} onClick={() => scrollToQuestion(ctrl.firstIndex)}
                                  className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-left hover:bg-blue-50 group transition-colors">
                                  <span className={`text-[9px] shrink-0 ${complete ? "text-emerald-500" : partial ? "text-blue-400" : "text-slate-300"}`}>
                                    {complete ? "●" : partial ? "◐" : "○"}
                                  </span>
                                  <span className="text-[11px] font-mono text-slate-500 truncate flex-1 group-hover:text-blue-700 transition-colors">{ctrl.control_id}</span>
                                  <span className="text-[10px] text-slate-400 tabular-nums shrink-0">{ctrlAnswered}/{ctrlTotal}</span>
                                </button>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </nav>
              </div>

              {/* Score summary (only when scored) */}
              {scoreResult && (
                <div className="px-4 py-3 border-t border-slate-100">
                  <p className="text-[9px] font-bold text-slate-400 tracking-widest uppercase mb-2">Score</p>
                  <div className="flex items-baseline gap-2">
                    <span className="text-2xl font-bold text-slate-900 tabular-nums">{scoreResult.overall_score}%</span>
                    <span className="text-[10px] text-slate-400">L{scoreResult.maturity_level} · {findings.length} finding{findings.length !== 1 ? "s" : ""}</span>
                  </div>
                </div>
              )}

              {/* AI Assistance */}
              <div className="px-3 py-3 border-t border-slate-100">
                <button onClick={() => setActiveTab("ai")} className="w-full btn-secondary text-xs justify-center py-2">
                  <Wand2 size={13} /> AI Assistance
                </button>
              </div>
            </div>

            {/* Center: questions grouped by domain → category */}
            <div id="qscroll" className="flex-1 overflow-y-auto">
              <div className="max-w-2xl mx-auto p-5 space-y-7">
                {saving && (
                  <p className="text-[10px] text-blue-500 sticky top-0 z-10">● Auto-saving…</p>
                )}

                {/* Active filter banner */}
                {filterMode && (() => {
                  const visibleCount = questions.filter(questionVisible).length;
                  const label = filterMode === "prefilled" ? "pre-filled" : filterMode === "ai_followup" ? "AI follow-up" : filterMode === "answered" ? "manually answered" : "unanswered";
                  return (
                    <div className="flex items-center justify-between bg-slate-100 rounded-xl px-3 py-2 text-[11px]">
                      <span className="text-slate-600">Showing <span className="font-bold text-slate-900">{visibleCount}</span> {label} question{visibleCount !== 1 ? "s" : ""}</span>
                      <button onClick={() => setFilterMode(null)} className="text-slate-400 hover:text-slate-700 font-medium">× Clear</button>
                    </div>
                  );
                })()}

                {domainGroups.map((group) => {
                  const groupVisible = group.indices.some((i) => questionVisible(questions[i]));
                  if (!groupVisible) return null;
                  return (
                  <section key={group.code} id={`dom-${group.code}`} className="space-y-4 scroll-mt-4">
                    <div className="flex items-center gap-2 pb-1 border-b border-slate-100">
                      <span className="badge badge-neutral font-mono">{group.code}</span>
                      <h3 className="text-sm font-bold text-slate-700 truncate">{group.name}</h3>
                      <span className="text-[10px] text-slate-400 ml-auto tabular-nums">
                        {group.indices.filter((i) => responses[questions[i]?.id]).length}/{group.indices.length}
                      </span>
                    </div>

                    {group.categories.map((cat) => {
                      const catVisible = cat.indices.some((i) => questionVisible(questions[i]));
                      if (!catVisible) return null;
                      return (
                      <div key={cat.code} className="space-y-3">
                        {cat.name && (
                          <div className="pl-1">
                            <div className="flex items-center gap-2">
                              <span className="w-1.5 h-1.5 rounded-full bg-blue-400" />
                              <h4 className="text-xs font-bold text-slate-600 uppercase tracking-wide">{cat.name}</h4>
                            </div>
                            {cat.criteria && (
                              <p className="text-[11px] text-slate-500 leading-snug mt-1 ml-3.5 border-l-2 border-blue-200 pl-2.5 bg-blue-50/40 py-1.5 rounded-r-lg">
                                <span className="font-semibold text-slate-600">Criteria evaluated: </span>{cat.criteria}
                              </p>
                            )}
                          </div>
                        )}

                        {cat.indices.map((i) => {
                          const qq = questions[i];
                          if (!qq || !questionVisible(qq)) return null;
                          const isPrefilled = prefilledIds.has(qq.id);
                          const cardCls = isPrefilled
                            ? "bg-yellow-50 border-yellow-300"
                            : "bg-white border-slate-100";
                          return (
                            <div key={qq.id} id={`q-${i}`} className={`border rounded-2xl p-5 space-y-3 ${cardCls}`} style={{ boxShadow: "var(--shadow-card)" }}>
                              <div className="flex items-center gap-2 flex-wrap">
                                <span className="text-[11px] font-bold text-slate-400 tabular-nums">{i + 1}</span>
                                {qq.control_id && <span className="badge badge-low font-mono">{qq.control_id}</span>}
                                {qq.ai && <span className="badge badge-medium flex items-center gap-1"><Sparkles size={10} /> AI follow-up</span>}
                                {isPrefilled && <span className="badge badge-medium flex items-center gap-1"><Sparkles size={10} /> Pre-filled</span>}
                                {responses[qq.id] && !isPrefilled && <span className="badge badge-success ml-auto">Answered</span>}
                              </div>
                              {qq.statement && (
                                <p className="text-xs text-slate-500 leading-relaxed border-l-2 border-blue-200 pl-3 bg-blue-50/50 py-2 rounded-r-lg">{qq.statement}</p>
                              )}
                              <p className="text-sm font-semibold text-slate-900 leading-relaxed">{qq.question}</p>
                              <QuestionInput question={qq} value={responses[qq.id] || ""} onChange={(v) => saveAnswer(qq, v)} readOnly={!canAnswer} />
                              {canComment && (
                                <div>
                                  <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block mb-1.5">Notes</label>
                                  <textarea value={notes[qq.id] || ""}
                                    onChange={(e) => setNotes((p) => ({ ...p, [qq.id]: e.target.value }))}
                                    onBlur={() => saveNote(qq)} placeholder="Add notes…" rows={2}
                                    className="w-full rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700 outline-none focus:border-blue-400 focus:bg-white resize-none transition-all placeholder:text-slate-300" />
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                      );
                    })}
                  </section>
                  );
                })}

                <p className="text-[10px] text-slate-400 flex items-center gap-1.5">
                  <FolderOpen size={11} className="text-slate-300" />
                  Documents are managed in the <button type="button" onClick={() => setActiveTab("engagement")} className="font-semibold text-blue-600 hover:underline">Engagement Documents</button> tab.
                  Use <button type="button" onClick={() => setActiveTab("ai")} className="font-semibold text-blue-600 hover:underline">AI Assistance</button> to pre-fill and generate follow-ups.
                </p>

                <div className="flex items-center justify-end pt-2 pb-8">
                  {showScore ? (
                    <button onClick={handleScore} disabled={scoring || answeredCount === 0} className="btn-primary px-6">
                      <Award size={14} /> {scoring ? "Scoring…" : "Submit & Score"}
                    </button>
                  ) : showSubmit ? (
                    <button onClick={handleSubmit} disabled={submitting} className="btn-primary px-6">
                      <Send size={14} /> {submitting ? "Submitting…" : "Submit for Review"}
                    </button>
                  ) : null}
                </div>
              </div>
            </div>

          </div>
          )
        ) : (
          <div className="h-full overflow-y-auto">
            {activeTab === "documents" && (
              <RequiredDocumentsTab assessmentId={assessment.id}
                canRequest={canDo(role, "requestDocuments")}
                canProvide={canDo(role, "provideDocuments")} />
            )}
            {activeTab === "engagement" && <EngagementDocumentsTab assessmentId={assessment.id} canUpload={canUpload} preAssessmentFileName={preAssessmentFileName} />}
            {activeTab === "ai" && (
              <AiAssistanceTab assessmentId={assessment.id} canAnswer={canAnswer}
                onPrefill={handlePrefill} prefilling={prefilling}
                onChanged={loadData} />
            )}
            {activeTab === "compliance" && <ComplianceTab assessmentId={assessment.id} role={role} />}
          </div>
        )}
      </div>
    </div>
  );
}

