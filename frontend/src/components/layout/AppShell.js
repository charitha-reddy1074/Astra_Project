"use client";

import { useState, useEffect } from "react";
import Sidebar    from "./Sidebar";
import Topbar     from "./Topbar";
import LoginView  from "@/components/auth/LoginView";
import DashboardView      from "@/components/workspace/DashboardView";
import AssessmentsView    from "@/components/workspace/AssessmentsView";
import KnowledgeBaseView  from "@/components/workspace/KnowledgeBaseView";
import ReportsView        from "@/components/workspace/ReportsView";
import OverallReportView  from "@/components/workspace/OverallReportView";
import AskCyberAIView     from "@/components/workspace/AskCyberAIView";
import OrganizationsView  from "@/components/workspace/OrganizationsView";
import OwnersView         from "@/components/workspace/OwnersView";
import InboxView           from "@/components/workspace/InboxView";
import OwnerDashboard      from "@/components/workspace/OwnerDashboard";
import HelpView            from "@/components/workspace/HelpView";
import ActivityLogView     from "@/components/workspace/ActivityLogView";
import { getRole, clearRole, canView } from "@/lib/auth";

export default function AppShell() {
  const [role, setRoleState]    = useState(undefined);
  const [activeView, setActive] = useState("dashboard");

  useEffect(() => { setRoleState(getRole()); }, []);

  // Default landing per role
  const defaultView = (r) => {
    if (r === "team_member" || r === "evidence_contributor") return "my_work";
    if (r === "auditor") return "assessments";
    return "dashboard";
  };

  const navigate = (view) => {
    if (!role || canView(role, view)) setActive(view);
  };

  const handleLogin = (r) => {
    setRoleState(r);
    setActive(defaultView(r));
  };

  const handleSignOut    = () => { clearRole(); setRoleState(null); setActive("dashboard"); };
  const handleSwitchRole = () => { clearRole(); setRoleState(null); };

  const renderView = () => {
    switch (activeView) {
      case "assessments":    return <AssessmentsView setActiveView={navigate} role={role} />;
      case "knowledge_base": return <KnowledgeBaseView role={role} />;
      case "reports":        return <ReportsView setActiveView={navigate} role={role} />;
      case "overall_report": return <OverallReportView role={role} />;
      case "ai":             return <AskCyberAIView role={role} />;
      case "organizations":  return <OrganizationsView />;
      case "users":          return <OwnersView role={role} />;
      case "inbox":          return <InboxView role={role} setActiveView={navigate} />;
      case "my_work":        return <OwnerDashboard role={role} setActiveView={navigate} />;
      case "activity":       return <ActivityLogView role={role} />;
      case "help":           return <HelpView role={role} />;
      default:               return <DashboardView setActiveView={navigate} role={role} />;
    }
  };

  if (role === undefined) return null;
  if (!role) return <LoginView onLogin={handleLogin} />;

  return (
    <div
      id="app-shell"
      className="flex h-screen overflow-hidden bg-slate-50"
      style={{ background: "linear-gradient(180deg, #fdfbf7 0%, #faf7f2 42%, #f7f2ea 100%)" }}
    >
      <div className="no-print">
        <Sidebar activeView={activeView} setActiveView={navigate} role={role} />
      </div>
      <div id="app-main" className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <div className="no-print">
          <Topbar
            activeView={activeView}
            role={role}
            onSignOut={handleSignOut}
            onSwitchRole={handleSwitchRole}
          />
        </div>
        <main className="flex-1 overflow-y-auto">{renderView()}</main>
      </div>
    </div>
  );
}
