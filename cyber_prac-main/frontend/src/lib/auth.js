const ROLE_KEY     = "cyberai_role";
const IDENTITY_KEY = "cyberai_identity";   // individual reviewer/contributor email

// Flat single-organization RBAC model. There is no central/market hierarchy —
// everyone belongs to one organization, and access is role-scoped within it.
//
//   org_owner            – Organization Owner: administration (users, catalog)
//   compliance_manager   – Compliance Manager: reviewer
//   security_manager     – Security Manager: reviewer
//   auditor              – Auditor / Reviewer: conducts assessments
//   team_member          – Team Member: participates in engagements
//   evidence_contributor – Evidence Contributor: provides evidence
//
// Reviewers (org_owner, compliance_manager, security_manager, auditor) see
// scores, findings and reports. Contributors (team_member,
// evidence_contributor) provide documents but never read evaluation output.
//
// `color` is the role chip class. Every role shares the ONE brand treatment
// (`side-avatar`: brand gradient, white initials) — roles are distinguished by
// their label, not by six competing hues.
export const ROLES = {
  org_owner:            { label: "Organization Owner",  description: "Administer users, roles & the framework catalog", color: "side-avatar"   },
  compliance_manager:   { label: "Compliance Manager",  description: "Run & oversee the compliance programme",          color: "side-avatar"   },
  security_manager:     { label: "Security Manager",    description: "Own security controls & review evidence",          color: "side-avatar"   },
  auditor:              { label: "Auditor / Reviewer",  description: "Conduct assigned assessments",                      color: "side-avatar"   },
  team_member:          { label: "Team Member",         description: "Participate in engagements & provide evidence",    color: "side-avatar"   },
  evidence_contributor: { label: "Evidence Contributor", description: "Provide evidence and pre-assessment documents",   color: "side-avatar"   },
};

// Role groups shared with the backend (backend/api/authz.py). Keep in sync.
export const REVIEWER_ROLES = ["org_owner", "compliance_manager", "security_manager", "auditor"];
export const CONTRIBUTOR_ROLES = ["team_member", "evidence_contributor"];
export const MANAGER_ROLES = ["org_owner", "compliance_manager", "security_manager"];

const REVIEWERS = REVIEWER_ROLES;
const EVERYONE = [...REVIEWER_ROLES, ...CONTRIBUTOR_ROLES];

// Sidebar items shown per role.
const NAV_ACCESS = {
  dashboard:      REVIEWERS,
  inbox:          EVERYONE,
  // Contributors do NOT get the full assessments console — "my_work" (My
  // Documents) is their surface. Granting both also rendered two identical
  // "My Documents" sidebar entries, since navLabel maps each to that name.
  assessments:    REVIEWERS,
  framework_assessment: REVIEWERS,
  reports:        REVIEWERS,
  overall_report: REVIEWERS,
  knowledge_base: EVERYONE,
  organizations:  ["org_owner"],                       // org management
  users:          ["org_owner"],                       // user management
  my_work:        CONTRIBUTOR_ROLES,
  ai:             EVERYONE,
  activity:       REVIEWERS,                           // immutable audit trail
  help:           EVERYONE,
};

// Granular workspace action permissions.
//  - org_owner: full write access plus all management actions
//  - compliance_manager & security_manager: management actions (assign, user admin)
//  - auditor: full write access — answer, upload, score, report
//  - contributors: view assessment + provide requested/engagement documents
const WORKSPACE_PERMS = {
  answerQuestions:  REVIEWERS,
  uploadEvidence:   EVERYONE,
  comment:          REVIEWERS,
  submitAssessment: REVIEWERS,
  reviewResponses:  REVIEWERS,
  scoreAssessment:  REVIEWERS,
  generateFindings: REVIEWERS,
  generateReports:  REVIEWERS,
  requestDocuments: REVIEWERS,
  provideDocuments: CONTRIBUTOR_ROLES,
  assignAssessment: MANAGER_ROLES,
  usePrefill:       REVIEWERS,
  // Part 4: running the assessment pipeline persists new evaluation + semantic +
  // memory records, so it is gated to reviewers server-side and here.
  runCompliancePipeline: REVIEWERS,
};

const WRITE_ROLES = {
  framework:     ["org_owner"],
  assessment:    MANAGER_ROLES,
  response:      REVIEWERS,
  finding:       REVIEWERS,
  organization:  ["org_owner"],
  questionnaire: ["org_owner"],
  user:          ["org_owner"],                          // role / user management
};

const DELETE_ROLES = {
  framework:    ["org_owner"],
  assessment:   ["org_owner"],
  finding:      ["org_owner"],
  organization: ["org_owner"],
  user:         ["org_owner"],
};

export function getRole()   { if (typeof window === "undefined") return null; try { return localStorage.getItem(ROLE_KEY) || null; } catch { return null; } }
export function setRole(r)  { localStorage.setItem(ROLE_KEY, r); emitRoleChange(); }
export function clearRole() { localStorage.removeItem(ROLE_KEY); clearIdentity(); emitRoleChange(); }

/* The signed-in role lives in localStorage, so it is an *external store*.
   AppShell reads it through useSyncExternalStore rather than copying it into
   React state inside an effect — no cascading render, and no hydration
   mismatch because the server snapshot is the distinct value `undefined`
   ("role not resolved yet") that the shell renders as an empty shell. */
const ROLE_EVENT = "cyberai:role";

function emitRoleChange() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(ROLE_EVENT));
}

export function subscribeRole(cb) {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(ROLE_EVENT, cb);
  window.addEventListener("storage", cb);            // signed in/out in another tab
  return () => {
    window.removeEventListener(ROLE_EVENT, cb);
    window.removeEventListener("storage", cb);
  };
}

/** Server snapshot: distinct from both `null` (signed out) and a real role. */
export function getServerRole() { return undefined; }

// Individual identity (email) — set for reviewers and contributors on login,
// used to scope a view to the assessments that person is actually involved in.
export function getIdentity()  { if (typeof window === "undefined") return null; try { return localStorage.getItem(IDENTITY_KEY) || null; } catch { return null; } }
export function setIdentity(v) { if (v) localStorage.setItem(IDENTITY_KEY, v); }
export function clearIdentity(){ try { localStorage.removeItem(IDENTITY_KEY); } catch {} }

export function isReviewer(role)    { return REVIEWER_ROLES.includes(role); }
export function isContributor(role) { return CONTRIBUTOR_ROLES.includes(role); }

export function canView(role, navItem)    { return (NAV_ACCESS[navItem] || []).includes(role); }
export function canCreate(role, resource) { return (WRITE_ROLES[resource] || []).includes(role); }
export function canEdit(role, resource)   { return (WRITE_ROLES[resource] || []).includes(role); }
export function canDelete(role, resource) { return (DELETE_ROLES[resource] || []).includes(role); }
export function canDo(role, permission)   { return (WORKSPACE_PERMS[permission] || []).includes(role); }

// Contributors are scoped to the assessments they are involved in.
export function scopesToAssigned(role)    { return CONTRIBUTOR_ROLES.includes(role); }

// Role-contextual navigation labels.
export function navLabel(role, key) {
  const overrides = {
    auditor: { assessments: "My Assessments" },
  };
  const base = {
    dashboard: "Dashboard", knowledge_base: "Knowledge Base",
    assessments: "Third Party Vendor Management", framework_assessment: "Compliance Readiness", reports: "Reports", organizations: "Organizations",
    users: "Users", ai: "Ask AI",
    inbox: "Inbox", my_work: "My Work",
    overall_report: "Organization Analysis",
    activity: "Activity Log", help: "Getting Started",
  };
  return overrides[role]?.[key] ?? base[key] ?? key;
}

