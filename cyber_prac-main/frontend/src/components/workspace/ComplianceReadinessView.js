"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { FileUp, Play, Plus, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import { canCreate, canDo } from "@/lib/auth";
import ComplianceTab from "./ComplianceTab";

const MODULE_MARKER = "[compliance-readiness]";
const isFramework = (fw) => {
  const code = `${fw.code || ""} ${fw.name || ""}`.toLowerCase();
  if (/type\s*[- ]?2/i.test(code)) return false;
  return (code.includes("nist") && code.includes("csf")) || code.includes("soc2") || code.includes("soc 2");
};

export default function FrameworkAssessmentView({ role }) {
  const [frameworks, setFrameworks] = useState([]);
  const [assessments, setAssessments] = useState([]);
  const [selected, setSelected] = useState(null);
  const [frameworkIds, setFrameworkIds] = useState([]);
  const [name, setName] = useState("");
  const [organization, setOrganization] = useState("");
  const [files, setFiles] = useState([]);
  const [report, setReport] = useState(null);
  const [catalog, setCatalog] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [mappingIndex, setMappingIndex] = useState([]);
  const [uploadMetrics, setUploadMetrics] = useState({ documents_processed: 0, duplicate_documents_skipped: 0, evidence_items_extracted: 0 });
  const [activeTab, setActiveTab] = useState("controls");
  const [evaluationRefresh, setEvaluationRefresh] = useState(0);
  const allowCreate = canCreate(role, "assessment");
  const canReviewMappings = canDo(role, "runCompliancePipeline");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [fws, all] = await Promise.all([api.frameworks.list(), api.assessments.list()]);
      setFrameworks(fws.filter(isFramework));
      setAssessments(all.filter((a) => (a.description || "").includes(MODULE_MARKER) || (a.description || "").includes("[framework-evidence-assessment]")));
    } catch (e) { setError(e.message); }
  }, []);
  useEffect(() => {
    let active = true;
    Promise.resolve().then(() => active && load());
    return () => { active = false; };
  }, [load]);

  const frameworkOptions = useMemo(() => frameworks.map((fw) => ({
    ...fw,
    label: `${fw.name}${/soc\s?2/i.test(`${fw.code} ${fw.name}`) && !/type\s*[- ]?1/i.test(`${fw.code} ${fw.name}`) ? " Type 1" : ""}`,
  })), [frameworks]);

  const startAssessment = async () => {
    if (!name.trim() || !frameworkIds.length) return;
    setBusy(true); setError("");
    try {
      const created = await api.assessments.create({
        name: name.trim(), organization: organization.trim() || null,
        framework_ids: frameworkIds, selected_domains: [],
        description: `${MODULE_MARKER} Framework-first evidence assessment`,
        generation_config: { strategy: "framework_only" },
      });
      await selectAssessment(created); await load(); setName(""); setReport(null);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const upload = async () => {
    if (!files.length || !selected) return;
    setBusy(true); setError("");
    try {
      const result = await api.assessments.uploadEvidenceBulk(selected.id, files, "evidence");
      setUploadMetrics(current => ({
        documents_processed: current.documents_processed + (result.count || 0),
        duplicate_documents_skipped: current.duplicate_documents_skipped + (result.duplicate_documents_skipped || 0),
        evidence_items_extracted: current.evidence_items_extracted + (result.text_extracted || 0),
      }));
      setFiles([]);
      await loadDocuments(selected.id);
    }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const evaluate = async () => {
    if (!selected) return;
    setBusy(true); setError("");
    try {
      setReport(await api.assessments.runCompliancePipeline(selected.id, {}));
      setEvaluationRefresh(value => value + 1);
      await loadMappings(selected.id);
    }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const open = async (assessment) => {
    await selectAssessment(assessment);
    setReport(null); setError("");
    try { setReport(await api.assessments.complianceReport(assessment.id)); } catch { /* first run */ }
  };

  const loadDocuments = async (assessmentId) => {
    try {
      const data = await api.assessments.engagementDocuments(assessmentId);
      setDocuments(Object.values(data.by_type || {}).flat());
    } catch { setDocuments([]); }
  };

  const loadMappings = async (assessmentId) => {
    try { setMappingIndex(await api.assessments.evidenceIndex(assessmentId)); }
    catch { setMappingIndex([]); }
  };

  const reviewMapping = async (mappingId, action) => {
    if (!selected) return;
    setBusy(true); setError("");
    try {
      await api.assessments.reviewEvidenceMapping(selected.id, mappingId, action);
      await loadMappings(selected.id);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const selectAssessment = async (assessment) => {
    setSelected(assessment);
    setUploadMetrics({ documents_processed: 0, duplicate_documents_skipped: 0, evidence_items_extracted: 0 });
    const rows = await Promise.all((assessment.framework_ids || []).map(id => api.frameworks.get(id).catch(() => null)));
    setCatalog(rows.filter(Boolean));
    await loadDocuments(assessment.id);
    await loadMappings(assessment.id);
    setActiveTab("controls");
  };

  const controls = catalog.flatMap(fw => (fw.domains || []).flatMap(domain => {
    const categorized = (domain.categories || []).flatMap(category => (category.controls || []).map(control => ({ ...control, domain, category, framework: fw })));
    return categorized.length ? categorized : (domain.controls || []).map(control => ({ ...control, domain, framework: fw }));
  }));
  const evaluatedById = new Map((report?.frameworks || []).flatMap(group =>
    (group.controls || []).map(control => [control.control?.control_id, control])
  ));

  return <div className="mx-auto max-w-6xl p-6 space-y-6">
    <header className="flex items-center gap-3">
      <span className="icon-tile flex h-10 w-10 items-center justify-center rounded-xl"><ShieldCheck size={20} /></span>
      <div><h1 className="text-2xl font-bold text-slate-900">Compliance Readiness</h1>
        <p className="text-sm text-slate-500">Choose a framework, provide organizational evidence, and review control readiness and gaps.</p></div>
    </header>
    {error && <p role="alert" className="rounded-xl bg-rose-50 p-3 text-sm text-rose-700">{error}</p>}
    <div className="grid gap-5 lg:grid-cols-[320px_1fr]">
      <section className="rounded-2xl border border-slate-100 bg-white p-5 space-y-4">
        <h2 className="font-bold text-slate-900">Framework scope</h2>
        {selected ? (allowCreate ? <button onClick={() => { setSelected(null); setCatalog([]); setReport(null); setActiveTab("controls"); }} className="btn-secondary w-full justify-center">New readiness assessment</button> : null) : allowCreate ? <>
        <input className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm" placeholder="Assessment name" value={name} onChange={e => setName(e.target.value)} />
        <input className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm" placeholder="Organization" value={organization} onChange={e => setOrganization(e.target.value)} />
        <div className="space-y-2">{frameworkOptions.map(fw => <label key={fw.id} className="flex items-start gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={frameworkIds.includes(fw.id)} onChange={e => setFrameworkIds(ids => e.target.checked ? [...ids, fw.id] : ids.filter(id => id !== fw.id))} />
          <span>{fw.label} <span className="text-xs text-slate-400">({fw.total_controls} controls)</span></span>
        </label>)}</div>
        {!frameworkOptions.length && <p className="text-xs text-amber-700">NIST CSF and SOC 2 datasets are not loaded yet.</p>}
        <button className="btn-primary w-full justify-center" disabled={busy || !name.trim() || !frameworkIds.length} onClick={startAssessment}><Plus size={14} /> Create readiness assessment</button>
        </> : <p className="text-xs text-slate-500">Assessment scope and creation are managed by your organization owner.</p>}
        <div className="border-t border-slate-100 pt-4"><h3 className="mb-2 text-xs font-bold uppercase text-slate-400">Readiness assessments</h3>
          <div className="space-y-1">{assessments.map(a => <button key={a.id} onClick={() => open(a)} className={`w-full rounded-lg px-3 py-2 text-left text-sm ${selected?.id === a.id ? "bg-blue-50 text-blue-700" : "hover:bg-slate-50"}`}>{a.name}</button>)}</div>
        </div>
      </section>
      <section className="rounded-2xl border border-slate-100 bg-white p-5 space-y-5">
        {!selected ? <div className="py-12 text-center text-sm text-slate-400">Select frameworks and create a readiness assessment.</div> : <>
          <div><h2 className="text-lg font-bold text-slate-900">{selected.name}</h2><p className="text-xs text-slate-500">{selected.organization || "Organization"} · {catalog.map(fw => `${fw.name}${/soc\s?2/i.test(fw.name) ? " Type 1" : ""}`).join(" · ")}</p></div>
          <div className="flex gap-1 border-b border-slate-100">
            {[['controls','Controls'],['evidence','Evidence'],['evaluation','Evaluation & gaps']].map(([key,label]) => <button key={key} onClick={() => setActiveTab(key)} className={`border-b-2 px-3 py-2 text-xs font-semibold ${activeTab === key ? "border-blue-600 text-blue-700" : "border-transparent text-slate-500"}`}>{label}</button>)}
          </div>
          {activeTab === "controls" && <div className="max-h-[58vh] overflow-auto rounded-lg border border-slate-100"><table className="w-full text-left text-xs"><thead className="sticky top-0 bg-slate-50"><tr>{["Framework","Control ID","Control name","Requirement / description","Status"].map(h => <th key={h} className="px-3 py-2">{h}</th>)}</tr></thead><tbody>
            {controls.map(c => <tr key={`${c.framework.id}:${c.code}`} className="border-t border-slate-100"><td className="px-3 py-2">{c.framework.name}</td><td className="px-3 py-2 font-semibold">{c.code}</td><td className="px-3 py-2">{c.name}</td><td className="px-3 py-2">{c.statement || c.description || "—"}</td><td className="px-3 py-2">{evaluatedById.get(c.code)?.status || "NOT EVALUATED"}</td></tr>)}
          </tbody></table></div>}
          {activeTab === "evidence" && <>
          <div className="rounded-xl border-2 border-dashed border-slate-200 p-5">
            <p className="mb-3 text-sm font-semibold text-slate-700">Upload policies, SOPs, procedures, access and incident response documents, risk assessments, contracts, audit artifacts, configurations, screenshots, logs, reports, JSON, and other evidence.</p>
            <div className="flex flex-wrap items-center gap-3"><input type="file" multiple accept=".pdf,.doc,.docx,.rtf,.xls,.xlsx,.pptx,.txt,.csv,.md,.json,.xml,.yaml,.yml,.conf,.ini,.log,.png,.jpg,.jpeg" onChange={e => setFiles(Array.from(e.target.files || []))} />
              <button className="btn-secondary" disabled={busy || !files.length} onClick={upload}><FileUp size={14} /> Upload Evidence</button>
              <span className="text-xs text-slate-500">{files.length} selected</span></div>
          </div>
          <div className="space-y-2"><h3 className="text-sm font-bold text-slate-700">Uploaded documents</h3>
            <p className="text-[11px] text-slate-400">Processed {uploadMetrics.documents_processed} · duplicates skipped {uploadMetrics.duplicate_documents_skipped} · text extracted {uploadMetrics.evidence_items_extracted}</p>
            {!documents.length && <p className="text-xs text-slate-400">No evidence uploaded yet.</p>}
            {documents.map((doc, i) => <div key={doc.stored_name || i} className="flex justify-between rounded-lg border border-slate-100 px-3 py-2 text-xs"><span>{doc.original_name || doc.stored_name}</span><span className="text-slate-400">{doc.doc_type || "evidence"}</span></div>)}
          </div>
          <div className="space-y-2"><h3 className="text-sm font-bold text-slate-700">Document → control mappings</h3>
            {!mappingIndex.length && <p className="text-xs text-slate-400">Mappings appear after control evaluation.</p>}
            {mappingIndex.map(item => <article key={item.evidence_id} className="rounded-lg border border-slate-100 p-3">
              <div className="flex flex-wrap items-center gap-2"><span className="text-xs font-semibold text-slate-700">{item.evidence_source_name}</span><span className="badge badge-neutral">{item.nature_label || item.evidence_format || "evidence"}</span>{item.frameworks?.length > 1 && <span className="badge badge-success">Shared evidence</span>}</div>
              <ul className="mt-2 space-y-1">{(item.controls || []).map((mapping, index) => <li key={`${mapping.framework}:${mapping.control_code}:${index}`} className="text-[11px] text-slate-600">
                <span className="font-semibold">{mapping.framework} · {mapping.control_code}</span> · {Math.round((mapping.relevance_score || 0) * 100)}% · {mapping.review_status}
                <p className="text-slate-400">{mapping.mapping_reason}</p>
                {mapping.requires_review && canReviewMappings && <span className="mt-1 inline-flex gap-2"><button disabled={busy} onClick={() => reviewMapping(mapping.mapping_id, "confirm")} className="text-[10px] font-semibold text-emerald-700 hover:underline">Confirm mapping</button><button disabled={busy} onClick={() => reviewMapping(mapping.mapping_id, "reject")} className="text-[10px] font-semibold text-rose-700 hover:underline">Reject mapping</button></span>}
              </li>)}</ul>
            </article>)}
          </div>
          </>}
          {activeTab === "evaluation" && <div className="space-y-4">
            <button className="btn-primary" disabled={busy || !documents.length} onClick={evaluate}><Play size={14} /> {busy ? "Processing evidence and evaluating…" : "Evaluate Controls"}</button>
            {report?.summary && <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">{[
              ["Controls", report.summary.total || 0], ["Pass", report.summary.counts?.PASS || 0],
              ["Partial", report.summary.counts?.PARTIAL || 0], ["Fail", report.summary.counts?.FAIL || 0],
              ["Insufficient evidence", report.summary.counts?.INSUFFICIENT_EVIDENCE || 0],
            ].map(([label,value]) => <div key={label} className="rounded-lg border border-slate-100 p-2"><p className="text-[10px] text-slate-400">{label}</p><p className="text-lg font-bold text-slate-800">{value}</p></div>)}</div>}
            {report?.metrics && <p className="text-xs text-slate-500">Evidence items reviewed: {report.metrics.evidence_files_seen ?? 0} · semantic calls: {report.metrics.semantic_evaluations ?? 0} · cached results: {report.metrics.semantic_cache_hits ?? 0}.</p>}
            <ComplianceTab key={`${selected.id}:${evaluationRefresh}`} assessmentId={selected.id} role={role} />
          </div>}
        </>}
      </section>
    </div>
  </div>;
}
