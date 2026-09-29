"use client";

import { useState, useRef, useEffect } from "react";
import { ChevronDown, LogOut, UserCog, Menu, Sun, Moon } from "lucide-react";
import { ROLES, navLabel, getIdentity, isContributor } from "@/lib/auth";
import { useTheme } from "@/components/ui/ThemeProvider";

const PAGE_META = {
  dashboard:      { sub: "Security posture at a glance" },
  assessments:    { sub: "Manage and conduct assessments" },
  reports:        { sub: "Compliance scores and findings" },
  overall_report: { sub: "Programme coverage across organizations" },
  activity:       { sub: "Immutable audit trail of platform activity" },
  ai:             { sub: "Grounded in NIST · ISO · CIS · your own evidence" },
  knowledge_base: { sub: "Frameworks · Questionnaires · Policies" },
  organizations:  { sub: "Assessment coverage by organization" },
  users:          { sub: "Create and manage the organization's people and roles" },
  inbox:          { sub: "Your work queue — active, pending, and completed assessments" },
  my_work:        { sub: "Your assigned assessments and responsibilities" },
  help:           { sub: "Guides and answers for the whole platform" },
};

/* Breadcrumb root per section — keeps the topbar orienting on small screens
   where the subtitle is hidden. */
const SECTION = {
  dashboard: "Overview", inbox: "Overview", assessments: "Overview",
  reports: "Overview", overall_report: "Overview", ai: "Overview",
  knowledge_base: "Library", my_work: "My work",
  organizations: "Administration", users: "Administration", activity: "Administration",
  help: "Help",
};

export function ThemeToggle({ className = "" }) {
  const { theme, toggleTheme } = useTheme();
  const isDark = theme === "dark";
  return (
    <button
      type="button"
      onClick={toggleTheme}
      className={`theme-toggle ${className}`}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      aria-pressed={isDark}
      title={isDark ? "Light mode" : "Dark mode"}
    >
      <Sun size={16} className="icon-sun" strokeWidth={2} />
      <Moon size={16} className="icon-moon" strokeWidth={2} />
    </button>
  );
}

export default function Topbar({ activeView, role, onSignOut, onSwitchRole, onToggleSidebar }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const roleInfo = ROLES[role] || ROLES.org_owner;
  const title = navLabel(role, activeView) || "Dashboard";
  const meta = PAGE_META[activeView] || {};
  const section = SECTION[activeView];
  const identity = isContributor(role) ? getIdentity() : null;
  const initials = (roleInfo.label || "")
    .replace(/[^A-Za-z ]/g, "")
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w[0].toUpperCase())
    .slice(0, 2)
    .join("");

  useEffect(() => {
    const fn = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const esc = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", fn);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", fn);
      document.removeEventListener("keydown", esc);
    };
  }, []);

  return (
    <header className="topbar sticky top-0 z-30 h-[60px] shrink-0 px-4 sm:px-6 flex items-center justify-between gap-3">
      {/* Page identity — breadcrumb, then title, then subtitle */}
      <div className="flex min-w-0 items-center gap-3">
        {onToggleSidebar && (
          <button
            onClick={onToggleSidebar}
            aria-label="Open navigation"
            className="icon-btn lg:hidden -ml-1.5"
          >
            <Menu size={18} />
          </button>
        )}

        <div className="min-w-0">
          {section && (
            <nav aria-label="Breadcrumb" className="mb-0.5 hidden items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-[0.12em] sm:flex">
              <span style={{ color: "var(--brand-yellow)" }}>CyberAI</span>
              <span aria-hidden className="text-slate-300 dark:text-slate-600">/</span>
              <span className="truncate" style={{ color: "var(--text-muted)" }}>{section}</span>
            </nav>
          )}
          <h1 className="font-display text-[19px] sm:text-[22px] font-extrabold tracking-[-0.025em] leading-tight text-slate-900 truncate">
            {title}
          </h1>
          {meta.sub && (
            <p className="hidden sm:block text-[11.5px] font-medium leading-tight mt-0.5 text-slate-400 truncate">
              {meta.sub}
            </p>
          )}
        </div>
      </div>

      <div ref={ref} className="relative flex shrink-0 items-center gap-2">
        <ThemeToggle />

        {/* Profile / role menu */}
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-2 rounded-xl border px-1.5 py-1.5 pr-2.5 transition-all duration-200"
          style={{
            background: "var(--surface-primary)",
            borderColor: "var(--border-hairline)",
            boxShadow: "var(--shadow-xs)",
          }}
          aria-haspopup="menu"
          aria-expanded={open}
        >
          <span className="side-avatar" style={{ width: 28, height: 28, fontSize: 11 }}>
            {initials}
          </span>
          <span className="hidden sm:block text-[13px] font-semibold text-slate-800">{roleInfo.label}</span>
          <ChevronDown size={13} className="text-slate-400 transition-transform duration-200" style={{ transform: open ? "rotate(180deg)" : undefined }} />
        </button>

        {open && (
          <div className="menu-panel absolute right-0 top-full mt-2 w-56 z-50 p-0" role="menu">
            <div
              className="px-3.5 py-3"
              style={{
                background: "var(--brand-wash)",
                borderBottom: "1px solid var(--accent-soft-line)",
              }}
            >
              <p className="eyebrow" style={{ fontSize: 9 }}>Signed in as</p>
              <p className="mt-1 truncate font-display text-[13px] font-extrabold text-slate-900">
                {identity || roleInfo.label}
              </p>
              <p className="mt-0.5 truncate text-[10.5px] font-medium text-slate-500">
                {identity ? roleInfo.label : roleInfo.description}
              </p>
            </div>
            <div className="p-1.5">
              <button onClick={() => { setOpen(false); onSwitchRole?.(); }} className="menu-item" role="menuitem">
                <UserCog size={14} className="shrink-0" style={{ color: "var(--text-muted)" }} /> Switch Role
              </button>
              <button onClick={() => { setOpen(false); onSignOut?.(); }} className="menu-item menu-item-danger" role="menuitem">
                <LogOut size={14} className="shrink-0" /> Sign Out
              </button>
            </div>
          </div>
        )}
      </div>
    </header>
  );
}
