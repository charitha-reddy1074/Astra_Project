"""Simulated Prowler report generator.

The platform has no live cloud accounts, so this module stands in for the real
`prowler` CLI: given a document request's domain / evidence type it synthesizes a
Prowler-style scan report from the SAME predefined sample evidence the demo uses
(IAM access review, cloud secure score, SSO audit, RBAC policy, key management).

A report is generated once and handed back to the owner for preview; the owner
then chooses to submit it or discard it. Once written the text is never edited —
callers treat the returned content as immutable.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime

from backend.api.config import settings
from backend.core.utils import LLMClient

logger = logging.getLogger(__name__)

# ── Predefined evidence + check profiles ────────────────────────────────────
# Each profile carries the underlying evidence excerpt (reused from the demo
# sample docs) and a set of Prowler-style checks derived from it.

PROFILES: dict[str, dict] = {
    "iam": {
        "title": "Identity & Access Management",
        "service": "iam",
        "provider": "azure",
        "checks": [
            ("iam_mfa_enforced_for_privileged", "critical", "PASS",
             "MFA is enforced on 100% of privileged accounts (4 Global Admins, PIM-gated)."),
            ("iam_no_stale_accounts", "medium", "PASS",
             "0 accounts inactive >90 days — auto-disable policy enforced."),
            ("iam_service_accounts_no_interactive_login", "high", "PASS",
             "0 service accounts with interactive login; all converted to managed identity."),
            ("iam_guest_accounts_reviewed", "medium", "PASS",
             "12 active guest accounts, all approved and within 90-day expiry."),
            ("iam_access_recertification", "high", "PASS",
             "1,195 accounts certified this period; 38 revoked, 14 privilege reductions."),
        ],
        "evidence": """\
ACCESS REVIEW REPORT — Q2 2026
Period       : 2026-04-01 to 2026-06-30
SUMMARY
  Total accounts reviewed : 1,247   Revoked : 38 (3.1%)   Certified : 1,195
KEY FINDINGS
  - 100% of privileged accounts have MFA enforced
  - 0 service accounts with interactive login (managed identity)
  - Guest accounts: 12 active, all approved, within 90-day expiry
  - Stale accounts (>90 days inactive): 0 — auto-disable enforced
PRIVILEGED ACCESS
  Global Admins: 4 (named)   JIT approvals Q2: 847 approved / 12 denied""",
    },
    "rbac": {
        "title": "Role-Based Access Control",
        "service": "iam",
        "provider": "azure",
        "checks": [
            ("rbac_least_privilege_enforced", "high", "PASS",
             "Role categories scoped to least privilege; Global Admin ≤5 named, PIM-gated."),
            ("rbac_quarterly_access_review", "medium", "PASS",
             "Quarterly recertification via Azure AD Identity Governance."),
            ("rbac_access_auto_expiry", "medium", "PASS",
             "Grants auto-expire after 90 days unless renewed."),
            ("rbac_approval_workflow", "low", "PASS",
             "Access requests routed through ServiceNow with manager + security approval."),
        ],
        "evidence": """\
ROLE-BASED ACCESS CONTROL POLICY — v3.2   (Effective 2026-01-01)
ROLE CATEGORIES: Standard / Power / System Admin / Security Admin / Global Admin (<=5, PIM)
RBAC PLATFORM: Azure AD Entitlement Management — quarterly review cycle
ENTITLEMENT WORKFLOW: request -> manager approve -> security validate -> auto-expire 90d
COMPLIANCE: NIST 800-53 AC-2/AC-3/AC-6 | ISO 27001:2022 A.5.15 | CIS 5 & 6""",
    },
    "cloud": {
        "title": "Cloud Security Posture",
        "service": "defender",
        "provider": "azure",
        "checks": [
            ("cloud_secure_score_above_target", "high", "PASS",
             "Defender for Cloud secure score 82% (target 75%)."),
            ("cloud_encryption_at_rest", "critical", "PASS",
             "At-rest encryption AES-256 at 100% coverage."),
            ("cloud_encryption_in_transit", "high", "PASS",
             "In-transit TLS 1.2 enforced at 100%."),
            ("cloud_patch_critical_sla", "high", "PASS",
             "Critical patches applied within 72h at 98.7%."),
            ("cloud_app_security_baseline", "medium", "FAIL",
             "Application security area at 78% — below the 85% internal baseline."),
        ],
        "evidence": """\
CLOUD SECURITY POSTURE REPORT — 2026-07-01   (Microsoft Defender for Cloud)
SECURE SCORE: 82% (Target 75%)   Subscriptions: 4   Resources: 3,847
CONTROL AREA COMPLIANCE
  Identity & Access 91% | Network 87% | Data Protection 84% | Endpoint 89% | AppSec 78%
ENCRYPTION: at-rest AES-256 100% | in-transit TLS1.2 100% | CMK restricted 95%
PATCH: Critical(<72h) 98.7% | High(<30d) 97.1%""",
    },
    "network": {
        "title": "Network & SSO Security",
        "service": "entra",
        "provider": "azure",
        "checks": [
            ("sso_coverage_all_saas", "high", "PASS",
             "SSO covers 100% of approved SaaS apps (SAML 2.0 / OIDC)."),
            ("sso_mfa_conditional_access", "critical", "PASS",
             "Conditional Access requires MFA on all apps."),
            ("sso_block_legacy_auth", "high", "PASS",
             "Legacy authentication is blocked."),
            ("sso_require_compliant_device", "medium", "PASS",
             "Compliant-device policy enforced for app access."),
            ("sso_admin_portal_ip_restricted", "medium", "PASS",
             "Admin portal restricted to trusted IPs."),
        ],
        "evidence": """\
SSO CONFIGURATION AUDIT — ENTRA ID / 2026-07-15
SAML/OIDC APPS: Salesforce, ServiceNow, GitHub, Jira, Slack, Workday, AWS Dev — all PASS
CONDITIONAL ACCESS: Require MFA (all apps) ENABLED | Block Legacy Auth ENABLED
  Require Compliant Device ENABLED | Admin Portal trusted-IP ENABLED | High-risk sign-in -> Block
SSO Coverage: 100% of approved SaaS apps   Legacy auth blocked: Yes""",
    },
    "encryption": {
        "title": "Encryption & Key Management",
        "service": "keyvault",
        "provider": "azure",
        "checks": [
            ("kms_storage_encrypted", "critical", "PASS",
             "Storage accounts 100% AES-256, CMK for Restricted data."),
            ("kms_sql_tde_enabled", "high", "PASS",
             "Azure SQL 100% TDE enabled."),
            ("kms_disk_encryption", "high", "PASS",
             "VM disks 100% encrypted (Azure Disk Encryption)."),
            ("kms_no_hardcoded_secrets", "critical", "PASS",
             "234 active secrets in Key Vault; 0 hardcoded in code repos."),
            ("kms_tls_min_version", "medium", "PASS",
             "Minimum TLS 1.2 enforced via Azure Policy (deny < 1.2)."),
            ("kms_cert_expiry_monitored", "low", "FAIL",
             "3 certificates expiring < 90 days — auto-renewal on but flagged for review."),
        ],
        "evidence": """\
KEY MANAGEMENT & ENCRYPTION AUDIT — 2026-07-10   (Azure Key Vault + Purview)
ENCRYPTION COVERAGE: Storage 100% (AES-256, CMK) | SQL 100% (TDE) | VM disks 100%
KEY VAULT INVENTORY: 6 vaults | 47 certs (3 expiring <90d) | 234 secrets (0 hardcoded)
  Keys: 18 RSA-2048 + 4 EC P-256
TLS: min 1.2 (Azure Policy deny <1.2) | HSTS max-age=31536000""",
    },
}

DEFAULT_PROFILE = "cloud"

# Keyword → profile routing. First match wins, tested in this order.
_ROUTES: list[tuple[str, str]] = [
    ("rbac", "rbac"),
    ("role-based", "rbac"),
    ("role based", "rbac"),
    ("sso", "network"),
    ("network", "network"),
    ("firewall", "network"),
    ("conditional access", "network"),
    ("encrypt", "encryption"),
    ("key", "encryption"),
    ("kms", "encryption"),
    ("data protection", "encryption"),
    ("iam", "iam"),
    ("identity", "iam"),
    ("access review", "iam"),
    ("access", "iam"),
    ("mfa", "iam"),
    ("cloud", "cloud"),
    ("posture", "cloud"),
    ("secure score", "cloud"),
    ("m365", "cloud"),
    ("endpoint", "cloud"),
]


def _pick_profile(*signals: str | None) -> str:
    haystack = " ".join(s for s in signals if s).lower()
    for needle, profile in _ROUTES:
        if needle in haystack:
            return profile
    return DEFAULT_PROFILE


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "scan"


def _fallback_report(
    *,
    domain_code: str | None = None,
    domain_name: str | None = None,
    control_code: str | None = None,
    evidence_type: str | None = None,
    organization: str | None = None,
    control_statement: str | None = None,
    criteria_statement: str | None = None,
    expected_evidence: list[str] | None = None,
    assessor_note: str | None = None,
) -> tuple[str, str]:
    """Deterministic, offline report scoped to the specific evidence requirement.

    The profile is selected by keyword routing against the evidence_type, domain
    and control so the checks are relevant to what the assessor actually asked for.
    Used when no LLM is configured or the model call fails.
    """
    key = _pick_profile(evidence_type, domain_name, domain_code, control_code)
    profile = PROFILES.get(key, PROFILES[DEFAULT_PROFILE])
    checks = profile["checks"]
    passed = sum(1 for _, _, status, _ in checks if status == "PASS")
    failed = sum(1 for _, _, status, _ in checks if status == "FAIL")
    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")

    lines: list[str] = []
    lines.append("=" * 74)
    lines.append("  PROWLER SECURITY SCAN REPORT")
    lines.append("=" * 74)
    lines.append(f"  Provider        : {profile['provider']}")
    lines.append(f"  Service         : {profile['service']}")
    lines.append(f"  Assessment area : {profile['title']}")
    if organization:
        lines.append(f"  Organization    : {organization}")
    if control_code:
        lines.append(f"  Scoped control  : {control_code}")
    if evidence_type:
        lines.append(f"  Requested for   : {evidence_type}")
    if domain_name or domain_code:
        lines.append(f"  Domain          : {domain_name or ''} ({domain_code or 'n/a'})")
    lines.append(f"  Generated (UTC) : {generated_at}")
    lines.append(f"  Prowler version : 4.5.0 (simulated)")
    lines.append("")

    # Show the assessor's exact requirement so the owner can verify the scan
    # covers what was asked for.
    if assessor_note or control_statement or criteria_statement:
        lines.append("-" * 74)
        lines.append("  EVIDENCE REQUIREMENT")
        lines.append("-" * 74)
        if assessor_note:
            lines.append(f"  Assessor note      : {assessor_note}")
        if control_statement:
            for i, part in enumerate(control_statement.split("\n")):
                prefix = "  Control statement  : " if i == 0 else "                       "
                lines.append(prefix + part)
        if criteria_statement:
            for i, part in enumerate(criteria_statement.split("\n")):
                prefix = "  Criteria           : " if i == 0 else "                       "
                lines.append(prefix + part)
        if expected_evidence:
            lines.append("  Expected evidence  :")
            for ev in expected_evidence:
                lines.append(f"    • {ev}")
        lines.append("")

    lines.append("  SCAN SUMMARY")
    lines.append(f"    Total checks : {len(checks)}   PASS : {passed}   FAIL : {failed}")
    status_word = "PASS" if failed == 0 else "ATTENTION REQUIRED"
    lines.append(f"    Overall      : {status_word}")
    lines.append("")
    lines.append("-" * 74)
    lines.append("  FINDINGS")
    lines.append("-" * 74)
    for check_id, severity, status, detail in checks:
        marker = "[ PASS ]" if status == "PASS" else "[ FAIL ]"
        lines.append(f"  {marker}  {check_id}   (severity: {severity})")
        lines.append(f"           {detail}")
    lines.append("")
    lines.append("-" * 74)
    lines.append("  SOURCE EVIDENCE COLLECTED FROM OWNER ENVIRONMENT")
    lines.append("-" * 74)
    lines.append(profile["evidence"])
    lines.append("")
    lines.append("=" * 74)
    lines.append("  End of report — generated by Prowler engine (immutable).")
    lines.append("=" * 74)

    content = "\n".join(lines)
    short = uuid.uuid4().hex[:6]
    filename = f"Prowler_Scan_{_slug(evidence_type or profile['title'])[:40]}_{short}.txt"
    return filename, content


# ── LLM-generated report (tailored to the specific evidence requirement) ─────

_LLM_SYSTEM = """\
You are Prowler, the open-source security scanner. You output a single, realistic
PLAIN-TEXT scan report — exactly what the `prowler` CLI would print after running
against a cloud tenant. You never explain yourself, never use Markdown, and never
wrap the report in code fences. You emit only the report body.

The report MUST be scoped to the ONE specific evidence requirement given by the user.
You are collecting evidence from the OWNER's cloud environment solely for that
requirement. Every check must directly satisfy the assessor's stated evidence need —
do NOT include checks for unrelated services or controls. If the requirement is
about IAM access reviews, ALL checks are IAM/identity checks. If it's encryption,
ALL checks are KMS/TLS/at-rest checks. Never mix unrelated service areas.

Fabricated data is expected (there is no live account), but it must be plausible
and internally consistent, reflecting a mostly-compliant but realistic posture
(a small number of FAIL/INFO findings is fine).

Structure the report as:
  - A header block: report title, provider, service(s) scoped to the requirement,
    the control code, the requested evidence type, a generated-at timestamp,
    "prowler 4.5.0", and an "Evidence collected from: [owner environment]" line.
  - An EVIDENCE REQUIREMENT block repeating the assessor's note, control statement,
    and expected evidence types so the reader can verify coverage.
  - A SCAN SUMMARY line with total/PASS/FAIL counts and an overall verdict.
  - A FINDINGS section: 4-8 checks, each with a snake_case check_id, a severity
    (critical|high|medium|low), a status ([ PASS ]/[ FAIL ]/[ INFO ]), a concrete
    resource identifier (ARN / resource id / policy name), and a one-line detail.
  - A SOURCE EVIDENCE COLLECTED FROM OWNER ENVIRONMENT section with a brief excerpt
    of the raw evidence the scanner observed (configuration output, audit log
    snippet, policy text, etc.) that directly supports the findings above.
  - A short closing line noting the report is generated and immutable.
Keep it under ~70 lines."""


def _build_user_prompt(
    *, domain_code, domain_name, control_code, evidence_type,
    organization, control_statement, criteria_statement, expected_evidence,
    assessor_note, generated_at,
) -> str:
    parts = ["Generate a Prowler scan report collecting evidence ONLY for this requirement:\n"]
    parts.append(f"- Requested evidence type : {evidence_type or 'security configuration evidence'}")
    if domain_name or domain_code:
        parts.append(f"- Assessment domain       : {domain_name or ''} ({domain_code or 'n/a'})")
    if control_code:
        parts.append(f"- Scoped control          : {control_code}")
    if assessor_note:
        parts.append(f"- Assessor instruction    : {assessor_note}")
    if control_statement:
        parts.append(f"- Control requirement     : {control_statement}")
    if criteria_statement:
        parts.append(f"- Criteria               : {criteria_statement}")
    if expected_evidence:
        parts.append(f"- Expected evidence        : {'; '.join(expected_evidence)}")
    if organization:
        parts.append(f"- Organization            : {organization}")
    parts.append(f"- Generated at (UTC)      : {generated_at}")
    parts.append(
        "\nScan ONLY the services/resources relevant to the evidence type above. "
        "Every check must directly address the stated control requirement. "
        "Include a SOURCE EVIDENCE section showing raw evidence collected from the "
        "owner's environment that supports the findings. Output the report now."
    )
    return "\n".join(parts)


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        # Drop an opening ```lang line and a trailing ``` fence.
        t = re.sub(r"^```[a-zA-Z0-9]*\n", "", t)
        t = re.sub(r"\n```\s*$", "", t)
    return t.strip()


def _llm_report(
    *, domain_code, domain_name, control_code, evidence_type,
    organization, control_statement, criteria_statement, expected_evidence,
    assessor_note,
) -> tuple[str, str]:
    """Ask the LLM to generate a report tailored to this evidence requirement.

    Raises on any failure so the caller can fall back to the deterministic path.
    """
    if not settings.GROQ_API_KEY:
        raise RuntimeError("No GROQ_API_KEY configured")

    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")
    client = LLMClient(
        api_key=settings.GROQ_API_KEY,
        model=list(getattr(settings, "GROQ_MODEL_CHAIN", None) or [settings.GROQ_MODEL]),
        temperature=0.4,
    )
    user_prompt = _build_user_prompt(
        domain_code=domain_code, domain_name=domain_name, control_code=control_code,
        evidence_type=evidence_type, organization=organization,
        control_statement=control_statement, criteria_statement=criteria_statement,
        expected_evidence=expected_evidence, assessor_note=assessor_note,
        generated_at=generated_at,
    )
    raw = client.invoke(_LLM_SYSTEM, user_prompt, max_tokens=1800)
    content = _strip_fences(raw)
    if len(content) < 200:
        raise RuntimeError("LLM report too short to be usable")

    label = evidence_type or domain_name or control_code or "scan"
    filename = f"Prowler_Scan_{_slug(label)[:40]}_{uuid.uuid4().hex[:6]}.txt"
    return filename, content


def generate_report(
    *,
    domain_code: str | None = None,
    domain_name: str | None = None,
    control_code: str | None = None,
    evidence_type: str | None = None,
    organization: str | None = None,
    control_statement: str | None = None,
    criteria_statement: str | None = None,
    expected_evidence: list[str] | None = None,
    assessor_note: str | None = None,
) -> tuple[str, str]:
    """Return (filename, content) for a Prowler scan report scoped to the exact
    evidence requirement the assessor requested.

    Prefers an LLM-generated report — the LLM uses the control_statement,
    criteria_statement, expected_evidence, and assessor_note to collect evidence
    only from the relevant services/resources in the owner's environment.
    Falls back to the deterministic template report when no LLM is configured
    or the model call fails, so the feature always produces output.
    """
    try:
        return _llm_report(
            domain_code=domain_code, domain_name=domain_name, control_code=control_code,
            evidence_type=evidence_type, organization=organization,
            control_statement=control_statement, criteria_statement=criteria_statement,
            expected_evidence=expected_evidence, assessor_note=assessor_note,
        )
    except Exception as exc:  # noqa: BLE001 — any LLM/config failure → deterministic report
        logger.info("Prowler LLM generation unavailable (%s); using deterministic report", exc)
        return _fallback_report(
            domain_code=domain_code, domain_name=domain_name, control_code=control_code,
            evidence_type=evidence_type, organization=organization,
            control_statement=control_statement, criteria_statement=criteria_statement,
            expected_evidence=expected_evidence, assessor_note=assessor_note,
        )

