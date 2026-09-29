"use client";

import { useState } from "react";
import { BookOpen, FileText, Library, Database } from "lucide-react";
import FrameworksView     from "./FrameworksView";
import QuestionnairesView from "./QuestionnairesView";
import PolicyLibraryView  from "./PolicyLibraryView";
import ManageCatalogView  from "./knowledge_base/ManageCatalogView";
import { REVIEWER_ROLES, CONTRIBUTOR_ROLES } from "@/lib/auth";

const TABS = [
  { key: "frameworks",     label: "Frameworks",     icon: BookOpen, roles: REVIEWER_ROLES },
  { key: "questionnaires", label: "Questionnaires",  icon: FileText, roles: REVIEWER_ROLES },
  { key: "policies",       label: "Policies",        icon: Library,  roles: [...REVIEWER_ROLES, ...CONTRIBUTOR_ROLES] },
  // Organization-Owner-only framework catalog management (control CRUD,
  // validation, audit history, catalog search) — DB-backed.
  { key: "manage",         label: "Manage",          icon: Database, roles: ["org_owner"] },
];

export default function KnowledgeBaseView({ role }) {
  const visibleTabs = TABS.filter(t => t.roles.includes(role));
  const [activeTab, setActiveTab] = useState(visibleTabs[0]?.key ?? "policies");

  const renderContent = () => {
    switch (activeTab) {
      case "frameworks":     return <FrameworksView role={role} />;
      case "questionnaires": return <QuestionnairesView role={role} />;
      case "policies":       return <PolicyLibraryView role={role} />;
      case "manage":         return <ManageCatalogView role={role} />;
      default:               return null;
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Top tab nav */}
      <div className="shrink-0 bg-white border-b border-slate-200 px-6 flex items-center gap-1">
        {visibleTabs.map(tab => {
          const Icon   = tab.icon;
          const active = activeTab === tab.key;
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`flex items-center gap-2 px-4 py-3.5 text-[13px] font-medium border-b-2 transition-colors ${
                active
                  ? "border-blue-600 text-blue-700"
                  : "border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300"
              }`}
            >
              <Icon
                size={14}
                strokeWidth={active ? 2.5 : 1.8}
                className={active ? "text-blue-600" : "text-slate-400"}
              />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto">
        {renderContent()}
      </div>
    </div>
  );
}
