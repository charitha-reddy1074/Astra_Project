"use client";

import { useState } from "react";
import { ChevronLeft } from "lucide-react";
import { EngagementDocumentsTab, RequiredDocumentsTab } from "./WorkspaceTabContent";

// Contributor-facing assessment surface. Contributors provide supporting
// documentation ONLY — they never see assessment questions, AI pre-fill,
// scoring or reports. This view exposes just the two document tabs.
const TABS = [
  { key: "documents",  label: "Requested Documents" },
  { key: "engagement", label: "Upload Documents" },
];

export default function OwnerAssessmentView({ assessment, onBack }) {
  const [activeTab, setActiveTab] = useState("documents");

  return (
    <div className="flex flex-col h-full bg-slate-50">
      {/* Header */}
      <div className="shrink-0 bg-white border-b border-slate-100 px-4 py-2.5 flex items-center gap-3">
        <button onClick={onBack}
          className="flex items-center gap-1 text-xs font-medium text-slate-500 hover:text-blue-600 transition-colors shrink-0">
          <ChevronLeft size={13} /> Back
        </button>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-bold text-slate-900 truncate">{assessment.name}</p>
          {assessment.organization && <p className="text-[11px] text-slate-400 truncate">{assessment.organization}</p>}
        </div>
        {assessment.market_label && (
          <span className="shrink-0 text-[11px] font-semibold text-blue-700 bg-blue-50 rounded-lg px-2 py-1">
            {assessment.market_label}
          </span>
        )}
      </div>

      {/* Tabs */}
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
      <div className="flex-1 overflow-y-auto">
        {activeTab === "documents" && (
          <RequiredDocumentsTab assessmentId={assessment.id} canRequest={false} canProvide={true} />
        )}
        {activeTab === "engagement" && (
          <EngagementDocumentsTab assessmentId={assessment.id} canUpload={true} />
        )}
      </div>
    </div>
  );
}
