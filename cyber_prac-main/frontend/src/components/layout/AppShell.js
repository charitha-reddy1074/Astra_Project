"use client";

import { useState, useEffect, useCallback, useSyncExternalStore } from "react";
import Sidebar    from "./Sidebar";
import Topbar     from "./Topbar";
import LoginView  from "@/components/auth/LoginView";
import DashboardView      from "@/components/workspace/DashboardView";
import AssessmentsView    from "@/components/workspace/AssessmentsView";
import ComplianceReadinessView from "@/components/workspace/ComplianceReadinessView";
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
import { getRole, getServerRole, subscribeRole, clearRole, canView } from "@/lib/auth";

export default function AppShell() {
  /* The role is an external store (localStorage). Reading it through
     useSyncExternalStore keeps AppShell stateless w.r.t. sign-in — login,
     sign-out and "switch role" all route through lib/auth and re-render
     automatically. `undefined` is the server snapshot and renders the
     empty shell until the real role resolves on the client. */
  const role       = useSyncExternalStore(subscribeRole, getRole, getServerRole);
  const [activeView, setActive] = useState("dashboard");
  const [navOpen, setNavOpen]   = useState(false);

  // Default landing per role
  const defaultView = (r) => {
    if (r === "team_member" || r === "evidence_contributor") return "my_work";
    if (r === "auditor") return "assessments";
    return "dashboard";
  };

  /* Views receive this as `setActiveView`. The role guard from the original
     implementation is preserved: a target the current role cannot see is
     dropped rather than rendered. */
  const navigateView = useCallback((view) => {
    const r = getRole();
    if (!r || canView(r, view)) setActive(view);
  }, []);

  const handleLogin = (r) => setActive(defaultView(r));

  const handleSignOut = () => { clearRole(); setActive("dashboard"); setNavOpen(false); };
  const handleSwitchRole = () => { clearRole(); setActive("dashboard"); setNavOpen(false); };

  const closeNav = useCallback(() => setNavOpen(false), []);

  // Escape closes the mobile drawer.
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") setNavOpen(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const renderView = () => {
    switch (activeView) {
      case "assessments":    return <AssessmentsView setActiveView={navigateView} role={role} />;
      case "framework_assessment": return <ComplianceReadinessView role={role} />;
      case "knowledge_base": return <KnowledgeBaseView role={role} />;
      case "reports":        return <ReportsView setActiveView={navigateView} role={role} />;
      case "overall_report": return <OverallReportView role={role} />;
      case "ai":             return <AskCyberAIView role={role} />;
      case "organizations":  return <OrganizationsView />;
      case "users":          return <OwnersView role={role} />;
      case "inbox":          return <InboxView role={role} setActiveView={navigateView} />;
      case "my_work":        return <OwnerDashboard role={role} setActiveView={navigateView} />;
      case "activity":       return <ActivityLogView role={role} />;
      case "help":           return <HelpView role={role} />;
      default:               return <DashboardView setActiveView={navigateView} role={role} />;
    }
  };

  if (role === undefined) return null;
  if (!role) return <LoginView onLogin={handleLogin} />;

  return (
    <div
      id="app-shell"
      className="dashboard-shell flex h-screen overflow-hidden bg-slate-50"
    >
      <div className="no-print">
        <Sidebar
          activeView={activeView}
          setActiveView={navigateView}
          role={role}
          open={navOpen}
          onClose={closeNav}
        />
      </div>

      <div id="app-main" className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <div className="no-print">
          <Topbar
            activeView={activeView}
            role={role}
            onSignOut={handleSignOut}
            onSwitchRole={handleSwitchRole}
            onToggleSidebar={() => setNavOpen((v) => !v)}
          />
        </div>
        <main className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">{renderView()}</main>
      </div>
    </div>
  );
}
