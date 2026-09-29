"use client";

import { useState, useRef, useEffect } from "react";
import { ChevronDown, LogOut, UserCog, Menu } from "lucide-react";
import { ROLES, navLabel, getIdentity, isContributor } from "@/lib/auth";

const PAGE_META = {
  dashboard:      { sub: "Security posture at a glance" },
  assessments:    { sub: "Manage and conduct assessments" },
  reports:        { sub: "Compliance scores and findings" },
  ai:             { sub: "Grounded in NIST · ISO · CIS · your own evidence" },
  knowledge_base: { sub: "Frameworks · Questionnaires · Policies" },
  organizations:  { sub: "Assessment coverage by organization" },
  users:          { sub: "Create and manage the organization's people and roles" },
  inbox:          { sub: "Your work queue — active, pending, and completed assessments" },
  my_work:        { sub: "Your assigned assessments and responsibilities" },
};

export default function Topbar({ activeView, role, onSignOut, onSwitchRole, onToggleSidebar }) {
  const [open, setOpen] = useState(false);
  const ref             = useRef(null);
  const roleInfo        = ROLES[role] || ROLES.org_owner;
  const title           = navLabel(role, activeView) || "Dashboard";
  const meta            = PAGE_META[activeView] || {};
  const identity        = isContributor(role) ? getIdentity() : null;
  const initials        = (roleInfo.label || "")
    .replace(/[^A-Za-z ]/g, "")
    .split(/\s+/)
    .filter(Boolean)
    .map(w => w[0].toUpperCase())
    .slice(0, 2)
    .join("");

  useEffect(() => {
    const fn = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", fn);
    return () => document.removeEventListener("mousedown", fn);
  }, []);

  return (
    <header className="topbar sticky top-0 z-30 h-14 px-4 sm:px-6 flex items-center justify-between shrink-0">
      {/* Page identity — gold rule on the left edge */}
      <div className="flex items-center gap-3 min-w-0">
        {onToggleSidebar && (
          <button
            onClick={onToggleSidebar}
            aria-label="Toggle navigation"
            className="lg:hidden rounded-lg p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700"
          >
            <Menu size={18} />
          </button>
        )}
        <span
          className="w-[3px] h-8 rounded-full shrink-0 hidden sm:block"
          style={{ background: "var(--grad-gold)" }}
        />
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-extrabold text-slate-900 tracking-tight leading-none truncate">
            {title}
          </h1>
          {meta.sub && (
            <p className="text-xs text-slate-400 font-medium leading-none mt-1.5 truncate">{meta.sub}</p>
          )}
        </div>
      </div>

      <div ref={ref} className="relative flex items-center gap-2 shrink-0">
        {/* Role indicator */}
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-2 pl-1.5 pr-2.5 py-2 rounded-xl border border-slate-200 bg-white hover:border-blue-300 hover:bg-blue-50/60 transition-all duration-200"
          style={{ boxShadow: "var(--shadow-xs)" }}
          aria-haspopup="menu"
          aria-expanded={open}
        >
          <span className={`rounded-lg flex items-center justify-center shrink-0 font-display text-xs font-extrabold ${roleInfo.color}`}
            style={{ width: 28, height: 28 }}>
            {initials}
          </span>
          <span className="text-[13px] font-bold text-slate-800">{roleInfo.label}</span>
          <ChevronDown size={13} className={`text-slate-400 transition-transform duration-200 ${open ? "rotate-180" : ""}`} />
        </button>

        {open && (
          <div className="menu-panel absolute right-0 top-full mt-2 w-52 z-50 p-0 overflow-hidden">
            <div className="px-3.5 py-3" style={{ background: "linear-gradient(180deg, #f5f9ff, #e8f0fe)", borderBottom: "1px solid var(--border-gold)" }}>
              <p className="eyebrow" style={{ fontSize: 9 }}>Signed in as</p>
              <p className="text-[13px] font-extrabold text-slate-900 mt-1 truncate font-display">{identity || roleInfo.label}</p>
              <p className="text-[10.5px] text-slate-500 mt-0.5 font-medium truncate">{identity ? roleInfo.label : roleInfo.description}</p>
            </div>
            <div className="p-1.5">
              <button
                onClick={() => { setOpen(false); onSwitchRole?.(); }}
                className="menu-item"
              >
                <UserCog size={14} className="text-slate-400 shrink-0" /> Switch Role
              </button>
              <button
                onClick={() => { setOpen(false); onSignOut?.(); }}
                className="menu-item menu-item-danger"
              >
                <LogOut size={14} className="shrink-0" /> Sign Out
              </button>
            </div>
          </div>
        )}
      </div>
    </header>
  );
}


