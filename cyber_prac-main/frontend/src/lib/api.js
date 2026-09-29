// Backend base URL. Priority:
//   1) NEXT_PUBLIC_API_BASE env var (explicit override)
//   2) same hostname as the page on port 8000 — so the app works whether opened
//      via localhost:3000 OR the LAN IP (e.g. 192.168.x.x:3000)
//   3) 127.0.0.1:8000 fallback for SSR / build time
const BASE =
  process.env.NEXT_PUBLIC_API_BASE ||
  (typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : "http://127.0.0.1:8000");

// Identity headers for the backend's per-organization authorization
// (authz.py). Read straight from localStorage (not lib/auth) to avoid an import
// cycle. This is the spoofable-by-design ceiling: it stops the honest UI from
// ever requesting another organization's data, not a determined attacker.
function authHeaders() {
  if (typeof window === "undefined") return {};
  try {
    const h = {};
    const email = localStorage.getItem("cyberai_identity");
    const role = localStorage.getItem("cyberai_role");
    if (email) h["X-User-Email"] = email;
    if (role) h["X-User-Role"] = role;
    return h;
  } catch { return {}; }
}

async function req(path, options = {}) {
  let res;
  try {
    // ...options FIRST: spreading it after `headers` would replace the whole
    // header object, silently dropping Content-Type and the identity headers.
    res = await fetch(`${BASE}${path}`, {
      ...options,
      headers: { "Content-Type": "application/json", ...authHeaders(), ...options.headers },
    });
  } catch (e) {
    // Network-level failure (backend down, wrong host, or CORS) — fetch rejects
    // with a generic "Failed to fetch", so add the URL for a clearer message.
    throw new Error(`Cannot reach backend at ${BASE}. Is the API server running? (${e.message})`);
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json();
}

// ── Frameworks ──────────────────────────────────────────────────────────────

export const api = {
  frameworks: {
    list: () => req("/frameworks"),
    get: (id) => req(`/frameworks/${id}`),
    delete: (id) => req(`/frameworks/${id}`, { method: "DELETE" }),
    loadExisting: () => req("/frameworks/load-existing", { method: "POST" }),
    upload: async (file) => {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${BASE}/frameworks/upload`, { method: "POST", body: form });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    importJson: async (file) => {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${BASE}/frameworks/import-json`, { method: "POST", body: form });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
  },

  assessments: {
    list: () => req("/assessments"),
    get: (id) => req(`/assessments/${id}`),
    create: (data) => req("/assessments", { method: "POST", body: JSON.stringify(data) }),
    delete: (id) => req(`/assessments/${id}`, { method: "DELETE" }),
    assign: (id, assignedTo) =>
      req(`/assessments/${id}/assign`, {
        method: "POST",
        body: JSON.stringify({ assigned_to: assignedTo || null }),
      }),
    uploadPreAssessment: async (id, file) => {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${BASE}/assessments/${id}/pre-assessment`, {
        method: "POST", body: form,
      });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    preAssessmentDownloadUrl: (id) => `${BASE}/assessments/${id}/pre-assessment/download`,
    defaultPreAssessmentUrl: () => `${BASE}/pre-assessment/template`,
    generateQuestionnaire: (id, generationConfig) =>
      req(`/assessments/${id}/generate-questionnaire`, {
        method: "POST",
        body: JSON.stringify(generationConfig ? { generation_config: generationConfig } : {}),
      }),
    saveResponse: (assessmentId, data) =>
      req(`/assessments/${assessmentId}/responses`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    score: (id) => req(`/assessments/${id}/score`, { method: "POST" }),
    submit: (id) => req(`/assessments/${id}/submit`, { method: "POST" }),
    findings: (id) => req(`/assessments/${id}/findings`),
    prefill: (id) =>
      req(`/assessments/${id}/prefill`, { method: "POST" }),
    report: (id) => req(`/assessments/${id}/report`),
    reportView: (id) => req(`/assessments/${id}/report-view`),
    // Assessment pipeline (Part 4): the write path evaluates + normalises +
    // semantic-reviews + memory-reviews every selected framework and persists the
    // recorded readings; the read-only report renders them with no model calls.
    runCompliancePipeline: (id, body = {}) =>
      req(`/assessments/${id}/assessment-pipeline`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    complianceReport: (id) => req(`/assessments/${id}/compliance-report`),
    evidenceIndex: (id) => req(`/assessments/${id}/evidence-index`),
    reviewEvidenceMapping: (id, mappingId, action) =>
      req(`/assessments/${id}/evidence-mappings/${mappingId}/review`, {
        method: "POST", body: JSON.stringify({ action }),
      }),
    evidenceRequirements: (id) => req(`/assessments/${id}/evidence-requirements`),
    engagementDocuments: (id) => req(`/assessments/${id}/engagement-documents`),
    aiRateControls: (id) =>
      req(`/assessments/${id}/ai/rate-controls`, { method: "POST" }),
    aiResults: (id) => req(`/assessments/${id}/ai/results`),
    uploadEvidenceBulk: async (assessmentId, files, docType = "evidence") => {
      const form = new FormData();
      for (const f of files) form.append("files", f);
      form.append("doc_type", docType);
      const res = await fetch(`${BASE}/assessments/${assessmentId}/evidence-bulk`, {
        method: "POST", body: form, headers: authHeaders(),
      });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    deleteEvidence: (assessmentId, fileName) =>
      req(`/assessments/${assessmentId}/evidence-bulk?file_name=${encodeURIComponent(fileName)}`, {
        method: "DELETE",
      }),
    documentRequests: (id) => req(`/assessments/${id}/document-requests`),
    createDocumentRequests: (id, items, requestedBy, note) =>
      req(`/assessments/${id}/document-requests`, {
        method: "POST",
        body: JSON.stringify({ items, requested_by: requestedBy, note }),
      }),
    provideDocuments: async (assessmentId, requestId, files, providedBy = null) => {
      const form = new FormData();
      for (const f of files) form.append("files", f);
      if (providedBy) form.append("provided_by", providedBy);
      const res = await fetch(
        `${BASE}/assessments/${assessmentId}/document-requests/${requestId}/provide`,
        { method: "POST", body: form, headers: authHeaders() }
      );
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    // Owner self-service: generate a (simulated) Prowler report for a request,
    // preview it, then submit the generated report as evidence.
    generateProwler: (assessmentId, requestId) =>
      req(`/assessments/${assessmentId}/document-requests/${requestId}/prowler`, {
        method: "POST",
      }),
    submitProwler: (assessmentId, requestId, reportId, providedBy = null) =>
      req(`/assessments/${assessmentId}/document-requests/${requestId}/prowler/submit`, {
        method: "POST",
        body: JSON.stringify({ report_id: reportId, provided_by: providedBy }),
      }),
    reviewDocumentRequest: (assessmentId, requestId, action, note) =>
      req(`/assessments/${assessmentId}/document-requests/${requestId}/review`, {
        method: "POST",
        body: JSON.stringify({ action, note }),
      }),
    deleteDocumentRequest: (assessmentId, requestId) =>
      req(`/assessments/${assessmentId}/document-requests/${requestId}`, {
        method: "DELETE",
      }),

    uploadEvidence: async (assessmentId, responseId, file) => {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(
        `${BASE}/assessments/${assessmentId}/evidence?response_id=${responseId}`,
        { method: "POST", body: form }
      );
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
  },

  documentRequests: {
    listAll: (status) =>
      req(`/document-requests${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  },

  // Admin-only framework catalog management (Knowledge Base → Manage). Backed by
  // the DB catalog endpoints in backend/api/routers/catalog.py; gated to
  // org_owner server-side via require_organization_owner. Headers ride along req().
  catalog: {
    stats:           ()            => req("/catalog/stats"),
    search:          (q, limit=50) => req(`/catalog/search?q=${encodeURIComponent(q)}&limit=${limit}`),
    validate:        (fwId)        => req(`/catalog/frameworks/${fwId}/validate`),
    exportFramework: (fwId)        => req(`/catalog/frameworks/${fwId}/export`),
    control: {
      get:       (pk)         => req(`/catalog/controls/${pk}`),
      history:   (pk)         => req(`/catalog/controls/${pk}/history`),
      create:    (fwId, data) => req(`/catalog/frameworks/${fwId}/controls`, { method: "POST", body: JSON.stringify(data) }),
      update:    (pk, data)   => req(`/catalog/controls/${pk}`, { method: "PATCH", body: JSON.stringify(data) }),
      // DELETE returns 204 (no body) — use a bare fetch so we don't res.json() an empty body.
      delete: async (pk) => {
        const res = await fetch(`${BASE}/catalog/controls/${pk}`, { method: "DELETE", headers: authHeaders() });
        if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
        return true;
      },
      duplicate: (pk)         => req(`/catalog/controls/${pk}/duplicate`, { method: "POST" }),
      move:      (pk, catId)  => req(`/catalog/controls/${pk}/move`, { method: "POST", body: JSON.stringify({ target_category_id: catId }) }),
    },
  },

  ai: {
    chat: (message, history = []) =>
      req("/ai/chat", { method: "POST", body: JSON.stringify({ message, history }) }),
  },

  // Activity log — the permanent, chronological record of major platform actions.
  // READ-ONLY on purpose: the backend (routers/activity.py) exposes GET routes
  // only, so there is deliberately no create/update/delete here to mirror.
  activity: {
    // params: { q, action, market, actor, from, to, limit, offset }
    // `from`/`to` accept YYYY-MM-DD (what <input type="date"> gives) or DD/MM/YYYY.
    list: (params = {}) => {
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(params)) {
        if (v !== undefined && v !== null && v !== "") qs.set(k, v);
      }
      const s = qs.toString();
      return req(`/activity${s ? `?${s}` : ""}`);
    },
    // { actions: [{action, count}], markets: [string] } — the filter dropdowns.
    facets: () => req("/activity/actions"),
  },

  users: {
    list:        (role)         => req(`/users${role ? `?role=${encodeURIComponent(role)}` : ""}`),
    getByRole:   (role)         => req(`/users/by-role/${encodeURIComponent(role)}`),
    get:         (email)        => req(`/users/${encodeURIComponent(email)}`),
    create:      (data)         => req("/users", { method: "POST", body: JSON.stringify(data) }),
    update:      (email, data)  => req(`/users/${encodeURIComponent(email)}`, { method: "PATCH", body: JSON.stringify(data) }),
    delete:      (email)        => req(`/users/${encodeURIComponent(email)}`, { method: "DELETE" }),
    stats:       ()             => req("/users-stats"),
  },
};

