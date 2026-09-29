"use client";

import { useState } from "react";
import { Shield, ClipboardCheck, User, ArrowRight, ArrowLeft, LogIn } from "lucide-react";
import { ROLES, setRole, setIdentity, REVIEWER_ROLES, CONTRIBUTOR_ROLES } from "@/lib/auth";
import { getUsersByRoles } from "@/lib/users";
import { LogoLockup } from "@/components/ui/Logo";

// Three login portals over the flat organization model:
//   • Organization Owner — administration (users, catalog)
//   • Reviewer            — Compliance Manager / Security Manager / Auditor
//   • Contributor         — Team Member / Evidence Contributor
// Reviewers and contributors both sign in with their own identity; only the
// Organization Owner enters directly.
const PORTALS = [
  { key: "org_owner",    icon: Shield,         accent: "brand-tile" },
  { key: "reviewer",     icon: ClipboardCheck, accent: "brand-tile" },
  { key: "contributor",  icon: User,           accent: "brand-tile" },
];

const PORTAL_COPY = {
  reviewer: {
    title: "Reviewer Portal",
    sub: "Continue as a Compliance Manager or Security Manager, or sign in as an Auditor to work on the assessments assigned to you.",
  },
  contributor: {
    title: "Contributor Portal",
    sub: "Select your account to access the documents and evidence assigned to your organization.",
  },
};

const FIELD =
  "w-full text-sm border border-slate-200 rounded-xl px-4 py-3 outline-none bg-white text-slate-900 font-medium transition-all placeholder:text-slate-400 focus:border-blue-500 focus:ring-[3.5px] focus:ring-blue-400/25";

function IdentityForm({ title, sub, icon: Icon, accent, shadow, people, fallbackEmail, cta, onSubmit }) {
  return (
    <div className="card card-accent p-6">
      <div className="flex items-center gap-3.5 mb-4">
        <div className={`w-11 h-11 ${accent} rounded-xl flex items-center justify-center shrink-0`}
          style={{ boxShadow: shadow }}>
          <Icon size={19} className="text-white" />
        </div>
        <div>
          <p className="text-[14px] font-extrabold text-slate-900 font-display tracking-tight">{title}</p>
          <p className="text-xs text-slate-500 mt-0.5 font-medium">{sub}</p>
        </div>
      </div>
      <PeopleSelect people={people} fallbackEmail={fallbackEmail} cta={cta} onSubmit={onSubmit} />
    </div>
  );
}

function PeopleSelect({ people, fallbackEmail, cta, onSubmit }) {
  const [email, setEmail] = useState("");
  return (
    <form
      onSubmit={(e) => { e.preventDefault(); if (email.trim()) onSubmit(email.trim().toLowerCase()); }}
      className="flex flex-col gap-3">
      {people.length > 0 ? (
        <select required value={email} onChange={(e) => setEmail(e.target.value)} className={FIELD}>
          <option value="">Select your account…</option>
          {people.map((u) => <option key={u.email} value={u.email}>{u.name} · {u.email}</option>)}
        </select>
      ) : (
        <input
          type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
          placeholder="your.email@company.com"
          className={FIELD} />
      )}
      <button type="submit" disabled={!email.trim()} className="btn-primary btn-lg">
        <LogIn size={15} /> {cta}
      </button>
    </form>
  );
}

export default function LoginView({ onLogin }) {
  const [portal, setPortal] = useState(null);       // "reviewer" or "contributor" sub-step
  const auditors       = portal === "reviewer"    ? getUsersByRoles(["auditor"]) : [];
  const contributors   = portal === "contributor" ? getUsersByRoles(CONTRIBUTOR_ROLES) : [];

  const enter = (role, identity) => {
    setRole(role);
    if (identity) setIdentity(identity);
    onLogin(role, identity || null);
  };

  const back = () => { setPortal(null); };

  return (
    <div className="relative min-h-screen flex flex-col items-center justify-center p-8 overflow-hidden">
      {/* Cool layered backdrop */}
      <div className="absolute inset-0 -z-10" style={{ background: "var(--grad-canvas)" }} />
      <div
        className="absolute inset-0 -z-10 texture-kraft opacity-60"
        style={{ maskImage: "radial-gradient(70% 60% at 50% 40%, #000 0%, transparent 100%)",
                 WebkitMaskImage: "radial-gradient(70% 60% at 50% 40%, #000 0%, transparent 100%)" }}
      />
      {/* Brand glow — yellow opening into hot pink */}
      <div
        className="absolute -z-10 rounded-full blur-3xl"
        style={{ width: 620, height: 620, top: -220, left: "50%", transform: "translateX(-50%)",
                 background: "radial-gradient(circle, rgba(255, 45, 141, 0.20) 0%, transparent 68%)" }}
      />
      {/* Warm glow */}
      <div
        className="absolute -z-10 rounded-full blur-3xl"
        style={{ width: 520, height: 520, bottom: -240, right: -120,
                 background: "radial-gradient(circle, rgba(255, 106, 0, 0.16) 0%, transparent 70%)" }}
      />
      {/* Yellow glow */}
      <div
        className="absolute -z-10 rounded-full blur-3xl"
        style={{ width: 480, height: 480, bottom: -200, left: -140,
                 background: "radial-gradient(circle, rgba(245, 183, 0, 0.14) 0%, transparent 70%)" }}
      />
      {/* Brand hairline at the very top of the page */}
      <div className="absolute top-0 left-0 right-0 h-[3px] -z-10" style={{ background: "var(--grad-gold)" }} />

      {/* Header */}
      <div className="mb-12 text-center fade-in-up">
        <div className="flex items-center justify-center mb-7">
          <LogoLockup size={52} stacked={false} />
        </div>

        <span className="badge badge-brand mb-5">
          <span className="status-dot status-completed" /> Enterprise Compliance Suite
        </span>

        <h1 className="display-lg text-slate-900 mb-3 mt-1">
          {portal ? PORTAL_COPY[portal].title : "Sign in"}
        </h1>
        <p className="text-slate-500 text-[15px] max-w-md mx-auto leading-relaxed font-medium">
          {portal
            ? PORTAL_COPY[portal].sub
            : "Choose your portal to enter the platform. Access and capabilities are scoped to your role."}
        </p>
      </div>

      {portal === "contributor" ? (
        /* Contributor sub-step: pick a Team Member or Evidence Contributor account */
        <div className="w-full max-w-md space-y-4 fade-in-up">
          <IdentityForm
            title="Sign in as a Contributor"
            sub="You'll see documents and evidence assigned to your organization."
            icon={User} accent="brand-tile"
            shadow="0 3px 10px -2px rgba(124,58,237,0.40)"
            people={contributors}
            cta="Sign in as Contributor"
            onSubmit={(email) => {
              const person = contributors.find((u) => u.email === email);
              enter(person?.role || "evidence_contributor", email);
            }} />

          <button onClick={back}
            className="flex items-center gap-1.5 text-xs font-bold text-slate-500 hover:text-blue-700 transition-colors mx-auto pt-1">
            <ArrowLeft size={13} /> Back to all portals
          </button>
        </div>
      ) : portal === "reviewer" ? (
        /* Reviewer sub-step: managers enter directly, auditors pick an identity */
        <div className="w-full max-w-md space-y-4 fade-in-up">
          <button
            onClick={() => enter("compliance_manager")}
            className="card card-hover card-accent card-accent-red w-full flex items-center gap-4 p-6 text-left">
            <div className="w-11 h-11 brand-tile rounded-xl flex items-center justify-center shrink-0"
              style={{ boxShadow: "0 3px 10px -2px rgba(37,99,235,0.40)" }}>
              <ClipboardCheck size={19} className="text-white" />
            </div>
            <div className="flex-1">
              <p className="text-[14px] font-extrabold text-slate-900 font-display tracking-tight">Continue as Compliance Manager</p>
              <p className="text-xs text-slate-500 mt-0.5 font-medium">Run the compliance programme, assign work, and use every assessment feature.</p>
            </div>
            <ArrowRight size={16} className="text-slate-300 shrink-0" />
          </button>

          <button
            onClick={() => enter("security_manager")}
            className="card card-hover card-accent w-full flex items-center gap-4 p-6 text-left">
            <div className="w-11 h-11 brand-tile rounded-xl flex items-center justify-center shrink-0"
              style={{ boxShadow: "0 3px 10px -2px rgba(14,116,144,0.40)" }}>
              <Shield size={19} className="text-white" />
            </div>
            <div className="flex-1">
              <p className="text-[14px] font-extrabold text-slate-900 font-display tracking-tight">Continue as Security Manager</p>
              <p className="text-xs text-slate-500 mt-0.5 font-medium">Own security controls, review evidence, and use every assessment feature.</p>
            </div>
            <ArrowRight size={16} className="text-slate-300 shrink-0" />
          </button>

          <IdentityForm
            title="Sign in as an Auditor"
            sub="You'll see only the assessments assigned to you."
            icon={User} accent="brand-tile"
            shadow="0 3px 10px -2px rgba(79,70,229,0.40)"
            people={auditors}
            cta="Sign in as Auditor"
            onSubmit={(email) => enter("auditor", email)} />

          <button onClick={back}
            className="flex items-center gap-1.5 text-xs font-bold text-slate-500 hover:text-blue-700 transition-colors mx-auto pt-1">
            <ArrowLeft size={13} /> Back to all portals
          </button>
        </div>
      ) : (
        /* Portal grid */
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-5 w-full max-w-3xl fade-in-up" style={{ animationDelay: "0.06s" }}>
          {PORTALS.map((p, i) => {
            const Icon = p.icon;
            const onClick = () => (p.key === "org_owner") ? enter("org_owner") : setPortal(p.key);
            const role = p.key === "org_owner" ? ROLES.org_owner
              : p.key === "reviewer"
                ? { label: "Reviewer", description: "Compliance & Security Managers and Auditors — scores, findings and reports" }
                : { label: "Contributor", description: "Team Members and Evidence Contributors — provide documents and evidence" };
            return (
              <button
                key={p.key} onClick={onClick}
                style={{ animationDelay: `${0.04 + i * 0.05}s` }}
                className={`card-in group relative card card-hover p-7 text-left focus:outline-none overflow-hidden`}>
                {/* Gold sweep on hover */}
                <span
                  className="pointer-events-none absolute inset-x-0 top-0 h-[3px] origin-left scale-x-0 group-hover:scale-x-100 transition-transform duration-300 ease-out"
                  style={{ background: "var(--grad-gold)" }}
                />
                <div className={`w-12 h-12 ${p.accent} rounded-2xl flex items-center justify-center mb-5 group-hover:scale-105 group-hover:-rotate-2 transition-transform duration-300`}
                  style={{ boxShadow: "var(--shadow-sm)" }}>
                  <Icon size={20} className="text-white" />
                </div>
                <p className="text-[15px] font-extrabold text-slate-900 mb-1.5 font-display tracking-tight">{role.label}</p>
                <p className="text-xs text-slate-500 leading-relaxed mb-5 font-medium">{role.description}</p>
                <div className="flex items-center gap-1.5 text-[11.5px] font-bold uppercase tracking-wider text-slate-400 group-hover:text-blue-700 transition-colors">
                  {p.key === "org_owner" ? "Enter workspace" : "Select account"}
                  <ArrowRight size={12} className="group-hover:translate-x-1 transition-transform duration-300" />
                </div>
              </button>
            );
          })}
        </div>
      )}

      <div className="mt-12 flex items-center gap-2.5 fade-in-up" style={{ animationDelay: "0.3s" }}>
        <Shield size={11} className="text-slate-300" />
        <p className="text-[10.5px] font-bold tracking-widest uppercase text-slate-400">
          Internal platform · Role-scoped access
        </p>
      </div>
    </div>
  );
}
