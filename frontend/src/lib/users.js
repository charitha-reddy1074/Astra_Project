// User directory — dual-mode:
//   • Sync reads (getUsers, getUsersByRole) hit localStorage cache — safe in any render context.
//   • Async API functions (apiGetUsers, apiCreateUser, etc.) hit the PostgreSQL backend.
//   • syncUsersFromApi() refreshes the cache; call it on app boot or after mutations.
//
// Flat single-organization model: a person belongs to one `organization` (the
// legacy `market` field is gone). Reviewers and contributors are the two groups.
import { api } from "@/lib/api";

const USERS_KEY = "cyberai_users";

export const ASSIGNABLE_ROLES = [
  { key: "compliance_manager",   label: "Compliance Manager" },
  { key: "security_manager",     label: "Security Manager" },
  { key: "auditor",              label: "Auditor / Reviewer" },
  { key: "team_member",          label: "Team Member" },
  { key: "evidence_contributor", label: "Evidence Contributor" },
];

// Presentation metadata per role. `org_owner` is display-only here — there is
// one Organization Owner and they are not provisionable through the directory
// (see ASSIGNABLE_ROLES), but a record can still surface with that role.
// Badge classes mirror ROLES[*].color in lib/auth.js. `color` is the same hue as
// a hex, for the SVG donut.
export const ROLE_META = {
  org_owner:            { label: "Organization Owner",  badge: "bg-slate-900 text-white",   color: "#0f172a", accent: "bg-slate-900"   },
  compliance_manager:   { label: "Compliance Manager",  badge: "bg-blue-700 text-white",    color: "#2563eb", accent: "bg-blue-700"    },
  security_manager:     { label: "Security Manager",    badge: "bg-cyan-700 text-white",    color: "#0e7490", accent: "bg-cyan-700"    },
  auditor:              { label: "Auditor / Reviewer",  badge: "bg-indigo-600 text-white",  color: "#4f46e5", accent: "bg-indigo-600"  },
  team_member:          { label: "Team Member",         badge: "bg-emerald-700 text-white", color: "#047857", accent: "bg-emerald-700" },
  evidence_contributor: { label: "Evidence Contributor", badge: "bg-violet-700 text-white", color: "#7c3aed", accent: "bg-violet-700"  },
};

export function roleLabel(role) { return ROLE_META[role]?.label || role || "—"; }

/** Reviewers read scores, findings and reports. */
export const REVIEWER_ROLES = ["org_owner", "compliance_manager", "security_manager", "auditor"];
/** Contributors provide evidence but never read evaluation output. */
export const CONTRIBUTOR_ROLES = ["team_member", "evidence_contributor"];

// ── Organization normalisation ───────────────────────────────────────────────

/**
 * Organizations are referenced inconsistently across the platform: as ids or
 * labels. Collapse them all to one comparable key.
 */
export function organizationKey(value) {
  if (!value) return "";
  return String(value).trim().toLowerCase().replace(/[\s\-›/]+/g, "_");
}

/** Resolve any organization reference against a getOrganizationList() array. */
export function resolveOrganization(value, organizationList = []) {
  const key = organizationKey(value);
  if (!key) return null;
  return (
    organizationList.find((m) => organizationKey(m.id) === key) ||
    organizationList.find((m) => organizationKey(m.label) === key) ||
    null
  );
}

/** Display label for any organization reference; falls back to the raw value. */
export function organizationLabelOf(value, organizationList = []) {
  return resolveOrganization(value, organizationList)?.label || value || null;
}

/**
 * The platform's organization list — the distinct `organization` labels
 * attached to people. There is no hierarchy tree: an organization is simply the
 * label spelled on a person record (and mirrored on assessments when they are
 * created). Entries keep the legacy { id, label, path } shape so existing
 * consumers keep working unchanged.
 */
export function getOrganizationList() {
  const seen = new Set();
  for (const u of read()) {
    const m = (u.organization || "").trim();
    if (m) seen.add(m);
  }
  return [...seen]
    .sort((a, b) => a.localeCompare(b))
    .map((label) => ({ id: label, label, path: label }));
}

// ── Derived directory analytics ──────────────────────────────────────────────

/**
 * Per-organization coverage: who reviews for this organization and who
 * contributes. Organizations with no reviewer (or no contributor) are the gaps
 * an administrator needs to see.
 */
export function buildCoverage(users, organizationList = []) {
  return organizationList.map((m) => {
    const key = organizationKey(m.id);
    const inOrg = users.filter(
      (u) => organizationKey(u.organization) === key || organizationKey(u.organization) === organizationKey(m.label)
    );
    const reviewers    = inOrg.filter((u) => REVIEWER_ROLES.includes(u.role));
    const contributors = inOrg.filter((u) => CONTRIBUTOR_ROLES.includes(u.role));
    return {
      ...m,
      reviewers,
      contributors,
      people: inOrg,
      hasReviewer:    reviewers.length > 0,
      hasContributor: contributors.length > 0,
      gap: reviewers.length === 0 || contributors.length === 0,
    };
  });
}

/**
 * Workload per user, derived client-side from the assessment and
 * document-request collections (no dedicated backend endpoint required).
 *   • reviewers     → assessments where assigned_to === their email
 *   • contributors  → document requests raised against their organization
 *   • all           → document requests they personally raised / fulfilled
 * Returns a map keyed by lowercase email.
 */
export function computeWorkload(users, assessments = [], docRequests = [], organizationList = []) {
  const map = {};
  const openStatuses = new Set(["draft", "in_progress", "in_review"]);

  for (const u of users) {
    const email = (u.email || "").toLowerCase();
    const okey  = organizationKey(organizationLabelOf(u.organization, organizationList) || u.organization);

    const assigned = assessments.filter((a) => (a.assigned_to || "").toLowerCase() === email);
    const orgRequests = okey
      ? docRequests.filter((d) => organizationKey(d.organization) === okey)
      : [];

    map[email] = {
      assessments:        assigned.length,
      assessmentsOpen:    assigned.filter((a) => openStatuses.has(a.status)).length,
      assessmentsDone:    assigned.filter((a) => a.status === "completed").length,
      assignedList:       assigned,
      docRequests:        orgRequests.length,
      docRequestsPending: orgRequests.filter((d) => d.status === "requested").length,
      docRequestList:     orgRequests,
      requestsRaised:     docRequests.filter((d) => (d.requested_by || "").toLowerCase() === email).length,
    };
  }
  return map;
}

/** Primary workload number shown in the directory table for a given role. */
export function primaryWorkload(user, workload) {
  const w = workload?.[(user.email || "").toLowerCase()];
  if (!w) return { value: 0, label: "—", pending: 0 };
  if (CONTRIBUTOR_ROLES.includes(user.role))
    return { value: w.docRequests, label: w.docRequests === 1 ? "doc request" : "doc requests", pending: w.docRequestsPending };
  return { value: w.assessments, label: w.assessments === 1 ? "assessment" : "assessments", pending: w.assessmentsOpen };
}

// ── Small UI utilities ───────────────────────────────────────────────────────

export function initials(name, email) {
  const src = (name || email || "?").trim();
  const parts = src.split(/[\s._@-]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

// Deterministic avatar tint so the same person always looks the same. Soft tints
// of the platform accent hues only (blue, cyan, violet, indigo, emerald, amber,
// rose, slate) — no new colour language.
const AVATAR_TINTS = [
  "bg-blue-100 text-blue-700", "bg-violet-100 text-violet-700", "bg-emerald-100 text-emerald-700",
  "bg-amber-100 text-amber-700", "bg-rose-100 text-rose-700", "bg-cyan-100 text-cyan-700",
  "bg-indigo-100 text-indigo-700", "bg-slate-100 text-slate-600",
];
export function avatarTint(seed = "") {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return AVATAR_TINTS[h % AVATAR_TINTS.length];
}

/** Copy text to the clipboard with a legacy fallback. Resolves to true/false. */
export async function copyText(text) {
  if (!text) return false;
  try {
    if (navigator?.clipboard?.writeText) { await navigator.clipboard.writeText(text); return true; }
  } catch { /* fall through to legacy path */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch { return false; }
}

/** Build a mailto: URL for one or many recipients. */
export function mailtoUrl(emails, subject) {
  const list = (Array.isArray(emails) ? emails : [emails]).filter(Boolean);
  if (list.length === 0) return null;
  const qs = subject ? `?subject=${encodeURIComponent(subject)}` : "";
  return `mailto:${list.join(",")}${qs}`;
}

/** Serialise directory rows to CSV (RFC-4180 quoting). */
export function usersToCsv(rows, columns) {
  const esc = (v) => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const head = columns.map((c) => esc(c.header)).join(",");
  const body = rows.map((r) => columns.map((c) => esc(c.value(r))).join(",")).join("\n");
  return `${head}\n${body}`;
}

export function downloadCsv(filename, csv) {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

// ── localStorage cache (synchronous) ─────────────────────────────────────────

function read() {
  if (typeof window === "undefined") return [];
  try { return JSON.parse(localStorage.getItem(USERS_KEY) || "[]"); } catch { return []; }
}

function write(list) {
  try { localStorage.setItem(USERS_KEY, JSON.stringify(list)); } catch {}
}

/** Synchronous read from localStorage cache — safe in any render context. */
export function getUsers()           { return read(); }
export function getUsersByRole(role) { return read().filter((u) => u.role === role); }
/** Every person holding one of the given roles (e.g. REVIEWER_ROLES). */
export function getUsersByRoles(roles = []) {
  const wanted = new Set(roles);
  return read().filter((u) => wanted.has(u.role));
}
export function getUsersByOrganization(organization, role = null) {
  // Organization may be persisted as either an id or a label depending on how
  // the record was seeded, so compare on a normalised key.
  const key = organizationKey(organization);
  return read().filter(
    (u) => (!role || u.role === role) && organizationKey(u.organization) === key
  );
}

/** Legacy sync write — kept for backward-compat; prefer apiCreateUser for new code. */
export function addUser({ name, email, role, organization }) {
  const e = (email || "").trim().toLowerCase();
  const n = (name || "").trim();
  if (!e) throw new Error("Email is required.");
  if (!n) throw new Error("Name is required.");
  if (!ASSIGNABLE_ROLES.some((r) => r.key === role)) throw new Error("Pick a valid role.");
  if (CONTRIBUTOR_ROLES.includes(role) && !organization)
    throw new Error("Contributors must be assigned to an organization.");
  const list = read();
  if (list.some((u) => u.email === e)) throw new Error(`A user with ${e} already exists.`);
  const user = { id: e, name: n, email: e, role, organization: organization || null };
  write([...list, user]);
  return user;
}

export function removeUser(email) {
  const e = (email || "").trim().toLowerCase();
  write(read().filter((u) => u.email !== e));
}

// ── API-backed (async, PostgreSQL) ───────────────────────────────────────────

/**
 * Fetch all users from the DB and refresh the localStorage cache.
 * Call on app mount or after any create/delete operation.
 */
export async function syncUsersFromApi() {
  if (typeof window === "undefined") return;
  try {
    const apiUsers = await api.users.list();
    // Normalise to the shape components expect: { id, name, email, role, organization }
    write(apiUsers.map((u) => ({
      id:           u.email,
      name:         u.name,
      email:        u.email,
      role:         u.role,
      organization: u.organization || null,
      job_title:    u.job_title || null,
      department:   u.department || null,
      is_active:    u.is_active !== false,
    })));
  } catch {
    // Backend offline — silent fallback to cached localStorage data.
  }
}

/** Fetch users from DB (async). Optionally filter by role. */
export async function apiGetUsers(role) {
  return role ? api.users.getByRole(role) : api.users.list();
}

/** Create a user in the DB, then sync the cache. */
export async function apiCreateUser({ name, email, role, organization, phone, department, job_title, notes }) {
  const user = await api.users.create({ name, email, role, organization, phone, department, job_title, notes });
  await syncUsersFromApi();
  return user;
}

/** Update a user in the DB, then sync the cache. */
export async function apiUpdateUser(email, patch) {
  const user = await api.users.update(email, patch);
  await syncUsersFromApi();
  return user;
}

/** Delete a user from the DB, then sync the cache. */
export async function apiDeleteUser(email) {
  await api.users.delete(email);
  syncUsersFromApi(); // fire-and-forget
}