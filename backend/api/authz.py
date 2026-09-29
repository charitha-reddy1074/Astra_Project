"""Application-layer authorization helpers.

The platform has a single flat organization (no central/market hierarchy). Access
is role-scoped within that one organization:

    X-User-Email : the logged-in user's email
    X-User-Role  : org_owner | compliance_manager | security_manager
                 | auditor | team_member | evidence_contributor

Identity is still taken from headers the frontend attaches and is therefore
spoofable (see below). What authoritative scoping exists lives at the service
layer next to the data it queries.

Policy:
- org_owner             → full access (bypass). Manages users and the catalog.
- compliance_manager / security_manager / auditor
                        → reviewers: see scores, findings, reports; run the
                          compliance pipeline and Hindsight; review evidence.
- team_member / evidence_contributor
                        → contributors: participate in engagements and provide
                          documents, but never read evaluation output.
- No X-User-Role at all (non-browser callers / SSR / tests) → permissive, for
  backward compatibility with honest non-header callers. This is the documented
  "spoofable ceiling"; real authentication (signed session/token) is tracked as
  a follow-up.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException

#: Every role that can exist on a person record, in the flat organization model.
ORGANIZATION_ROLES = {
    "org_owner",             # Organization Owner — administration
    "compliance_manager",    # Compliance Manager — reviewer
    "security_manager",      # Security Manager — reviewer
    "auditor",               # Auditor / Reviewer — conducts assessments
    "team_member",           # Team Member — participates in engagements
    "evidence_contributor",  # Evidence Contributor — provides evidence
}

#: Machine roles — not human accounts but AI Compliance Agent components that
#: surface in the org model (Evaluator / Memory / Advisor). Kept as a separate
#: namespace so a person can never be provisioned with them.
AGENT_ROLES = {"evaluator", "memory", "advisor"}

ADMIN_ROLES = {"org_owner"}
REVIEWER_ROLES = {"org_owner", "compliance_manager", "security_manager", "auditor"}
CONTRIBUTOR_ROLES = {"team_member", "evidence_contributor"}
PROVIDER_ROLES = REVIEWER_ROLES | CONTRIBUTOR_ROLES
#: Contributors are scoped to the assessments they are actually involved in
#: (see `AssessmentService.contributor_assessment_ids`).
CONTRIBUTOR_SCOPED_ROLES = CONTRIBUTOR_ROLES


@dataclass
class Caller:
    """Identity claimed by the request headers (spoofable — see module docstring)."""
    email: str | None = None
    role: str | None = None

    @property
    def is_reviewer(self) -> bool:
        """Reviewers (and the owner) may read scores, findings and reports."""
        return self.role in REVIEWER_ROLES

    @property
    def is_contributor(self) -> bool:
        """Contributors provide evidence but never see evaluation output."""
        return self.role in CONTRIBUTOR_ROLES

    @property
    def is_identified(self) -> bool:
        return self.role is not None


def get_caller(
    x_user_email: str | None = Header(default=None),
    x_user_role: str | None = Header(default=None),
) -> Caller:
    """FastAPI dependency: extract the (spoofable) caller identity from headers."""
    email = (x_user_email or "").strip().lower() or None
    role = (x_user_role or "").strip() or None
    return Caller(email=email, role=role)


def require_reviewer(caller: Caller = Depends(get_caller)) -> Caller:
    """Allow reviewers and the org owner; block contributors and other roles.

    Same spoofable-ceiling policy as the rest of the module: a caller with no
    X-User-Role header is treated as permissive (SSR / tests).
    """
    if caller.role is None:
        return caller
    if caller.role not in REVIEWER_ROLES:
        raise HTTPException(status_code=403, detail="Reviewer access required")
    return caller


def require_provider(caller: Caller = Depends(get_caller)) -> Caller:
    """Allow any identified organization member to provide documents.

    Used to gate the Prowler generate/submit and document-provide endpoints:
    contributors live here, and reviewers/owners may act on their behalf.
    """
    if caller.role is None:
        return caller
    if caller.role not in PROVIDER_ROLES:
        raise HTTPException(status_code=403, detail="Organization access required")
    return caller


def require_organization_owner(caller: Caller = Depends(get_caller)) -> Caller:
    """Guard for administration surfaces (user management, catalog writes).

    Policy (mirrors this module's convention): allow `org_owner`; reject any
    other IDENTIFIED role with 403; stay permissive when no X-User-Role header
    is present at all (SSR / non-browser callers / tests). This is the same
    spoofable ceiling documented above.
    """
    if caller.role is None:
        return caller  # unidentified — permissive, like get_caller's baseline
    if caller.role != "org_owner":
        raise HTTPException(status_code=403, detail="Organization Owner access required")
    return caller