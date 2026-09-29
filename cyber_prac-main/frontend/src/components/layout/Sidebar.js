"use client";

import {
  LayoutDashboard, ClipboardCheck, BarChart3, Sparkles, ShieldCheck,
  Building2, Users, Library, Inbox, Globe,
  ScrollText, LifeBuoy, X,
} from "lucide-react";
import { canView, ROLES, navLabel } from "@/lib/auth";
import { LogoLockup } from "@/components/ui/Logo";

const ALL_ITEMS = [
  { key: "dashboard",      icon: LayoutDashboard, group: "OVERVIEW"   },
  { key: "inbox",          icon: Inbox,           group: "OVERVIEW"   },
  { key: "assessments",    icon: ClipboardCheck,  group: "OVERVIEW"   },
  { key: "framework_assessment", icon: ShieldCheck, group: "OVERVIEW" },
  { key: "reports",        icon: BarChart3,        group: "OVERVIEW"   },
  { key: "overall_report", icon: Globe,            group: "OVERVIEW"   },
  { key: "ai",             icon: Sparkles,         group: "OVERVIEW"   },
  { key: "knowledge_base", icon: Library,          group: "LIBRARY"    },
  { key: "my_work",        icon: LayoutDashboard,  group: "MY WORK"    },
  { key: "organizations",  icon: Building2,        group: "ADMIN"      },
  { key: "users",          icon: Users,            group: "ADMIN"      },
  { key: "activity",       icon: ScrollText,       group: "ADMIN"      },
  { key: "help",           icon: LifeBuoy,         group: "HELP"       },
];

function getNavGroups(role) {
  const keys = ALL_ITEMS.map((i) => i.key).filter((k) => canView(role, k));
  const items = ALL_ITEMS.filter((i) => keys.includes(i.key));
  const map = {};
  for (const item of items) {
    if (!map[item.group]) map[item.group] = [];
    map[item.group].push(item);
  }
  return Object.entries(map).map(([label, items]) => ({ label, items }));
}

export default function Sidebar({ activeView, setActiveView, role, open, onClose }) {
  const roleInfo = ROLES[role] || ROLES.org_owner;
  const groups   = getNavGroups(role);

  return (
    <>
      {/* Scrim behind the mobile drawer only — desktop keeps the rail inline. */}
      {open && (
        <button
          aria-label="Close navigation"
          onClick={onClose}
          className="scrim lg:hidden no-print"
        />
      )}

      <aside
        aria-label="Primary navigation"
        className={[
          "sidebar flex h-screen w-[264px] shrink-0 flex-col select-none",
          "fixed inset-y-0 left-0 z-40 transition-transform duration-200 ease-out",
          "lg:static lg:z-auto lg:translate-x-0",
          open ? "translate-x-0 shadow-2xl" : "-translate-x-full",
        ].join(" ")}
      >
        {/* Brand */}
        <div className="flex h-[60px] shrink-0 items-center justify-between gap-2 px-5"
          style={{ borderBottom: "1px solid var(--chrome-border)" }}>
          <LogoLockup size={30} />
          <button onClick={onClose} aria-label="Close navigation" className="icon-btn lg:hidden -mr-1">
            <X size={16} />
          </button>
        </div>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto overflow-x-hidden px-3 py-5">
          {groups.map((group, gi) => (
            <div key={group.label} className={gi > 0 ? "mt-6" : ""}>
              <p className="nav-group-label">{group.label}</p>
              <div className="space-y-0.5">
                {group.items.map((item) => {
                  const Icon   = item.icon;
                  const label  = navLabel(role, item.key);
                  const active = activeView === item.key;
                  return (
                    <button
                      key={item.key}
                      onClick={() => { setActiveView(item.key); onClose?.(); }}
                      aria-current={active ? "page" : undefined}
                      className={`nav-item ${active ? "nav-item-active" : ""}`}
                    >
                      <Icon
                        size={18}
                        strokeWidth={active ? 2.1 : 1.8}
                        className="nav-icon transition-colors duration-200"
                        style={{ color: active ? "var(--brand-orange)" : "var(--text-muted)" }}
                      />
                      <span className="truncate">{label}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* Role footer */}
        <div className="shrink-0 px-3 pb-4 pt-3"
          style={{ borderTop: "1px solid var(--chrome-border)" }}>
          <div className="side-card flex items-start gap-2.5 px-3 py-2.5">
            <span className="side-avatar mt-0.5 h-6 w-6 px-1.5 text-[9.5px] leading-none">
              {(roleInfo.label || "")
                .replace(/[^A-Za-z ]/g, "")
                .split(/\s+/)
                .filter(Boolean)
                .map((w) => w[0].toUpperCase())
                .slice(0, 2)
                .join("")}
            </span>
            <span className="min-w-0">
              <span className="side-role block">{roleInfo.label}</span>
              <span className="side-note mt-0.5 block">{roleInfo.description}</span>
            </span>
          </div>
        </div>
      </aside>
    </>
  );
}
