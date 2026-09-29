"use client";

import {
  LayoutDashboard, ClipboardCheck, BarChart3, Sparkles,
  Building2, Users, Library, Inbox, Globe,
  ScrollText, LifeBuoy,
} from "lucide-react";
import { canView, ROLES, navLabel } from "@/lib/auth";
import { LogoLockup } from "@/components/ui/Logo";

const ALL_ITEMS = [
  { key: "dashboard",      icon: LayoutDashboard, group: "OVERVIEW"   },
  { key: "inbox",          icon: Inbox,           group: "OVERVIEW"   },
  { key: "assessments",    icon: ClipboardCheck,  group: "OVERVIEW"   },
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
  const roleInfo  = ROLES[role] || ROLES.org_owner;
  const groups    = getNavGroups(role);

  return (
    <aside
      className={[
        "flex h-screen w-[250px] shrink-0 flex-col select-none border-r border-slate-100 bg-white",
        "fixed inset-y-0 left-0 z-40 transition-transform duration-200",
        "lg:static lg:translate-x-0 lg:z-auto",
        open ? "translate-x-0 shadow-2xl" : "-translate-x-full",
        open ? "" : "lg:shadow-none",
      ].join(" ")}
    >
      {/* Brand */}
      <div className="h-14 flex items-center px-5 border-b border-slate-100 shrink-0">
        <LogoLockup size={30} />
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-6">
        {groups.map((group) => (
          <div key={group.label}>
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
                    className={`nav-item ${active ? "nav-item-active" : ""}`}
                  >
                    <Icon
                      size={18}
                      strokeWidth={active ? 2.4 : 1.9}
                      className={`nav-icon transition-colors duration-200 ${
                        active ? "text-blue-600" : "text-slate-400"
                      }`}
                    />
                    <span className="truncate">{label}</span>
                    {active && (
                      <span
                        className="ml-auto w-1.5 h-1.5 rounded-full shrink-0"
                        style={{ background: "var(--grad-gold)", boxShadow: "0 0 0 3px rgba(37,99,235,0.20)" }}
                      />
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* Role footer */}
      <div className="px-3 py-4 border-t border-slate-100 shrink-0">
        <div
          className="flex items-center gap-2.5 px-3 py-3 rounded-xl"
          style={{ background: "var(--color-slate-50)", border: "1px solid var(--color-hairline)" }}
        >
          <span className={`text-[10px] px-2 py-0.5 rounded-md font-bold shrink-0 ${roleInfo.color}`}>
            {roleInfo.label}
          </span>
          <span className="text-[10px] text-slate-500 truncate leading-tight font-medium">
            {roleInfo.description}
          </span>
        </div>
      </div>
    </aside>
  );
}

