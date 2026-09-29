import os
import json
import hashlib
import re
import uuid
from datetime import datetime
from sqlalchemy import select, delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.authz import CONTRIBUTOR_ROLES
from backend.api.repositories.assessment_repo import AssessmentRepository
from backend.api.repositories.framework_repo import FrameworkRepository
from backend.api.models.assessment import (
    Assessment, Questionnaire, Response, Evidence, FollowupQuestion, CategoryRating,
    DocumentRequest, Finding, Score,
)
from backend.api.models.user import User
from backend.api.schemas.assessment import AssessmentCreate, ResponseCreate
from backend.api.services.scoring_service import ScoringService, score_response
from backend.api.config import settings
from backend.core.evaluation_metrics import market_control_rollup


def _framework_key(code: str) -> str:
    """Map a framework code to an evaluation_metrics profile key."""
    return (code or "").lower().replace("_", "-")


def _safe_basename(name: str) -> str:
    """Reduce an uploaded file name to a safe basename.

    Strips directory components (POSIX and Windows) and clamps length so a
    malicious name like `..\\..\\x.py` or a 10k-char string can never escape the
    evidence folder or exhaust the filesystem.
    """
    name = (name or "upload").replace("\\", "/").split("/")[-1].strip()
    name = re.sub(r"[^\w.\- ]", "_", name)
    name = name.strip(". ")
    return (name or "upload")[:120]


def _norm_market(value: str | None) -> str:
    """Normalise a market id/label so the two spellings compare equal.

    Assessments carry `market_id` ("iom_bu", "united_states") while the users
    table carries the human label ("IOM BU", "United States"); stripping
    everything but alphanumerics makes both sides meet in the middle.
    """
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


# Plain-language guidance for the generic evidence-type labels an assessor can
# request. Keyed by the lowercased label; used when a request cannot be tied to
# a specific framework control (older requests carry no control_code).
EVIDENCE_TYPE_HINTS = {
    "policy": "Provide the approved, currently in-force policy document — include its version, owner and approval/last-review date.",
    "standard": "Provide the internal standard that makes the requirement mandatory, including its scope and any approved exceptions.",
    "procedure": "Provide the written procedure/runbook staff actually follow, showing its owner and last review date.",
    "process": "Provide the documented process, including who owns it, what triggers it and how often it runs.",
    "configuration": "Export the live configuration (settings report, screenshot or config file) showing the control is enabled and what it covers.",
    "report": "Provide the most recent report produced by the tool or process, dated and covering the assessment period.",
    "records": "Provide dated records showing the activity was actually carried out on its required cadence — the last few cycles is usually enough.",
    "logs": "Provide exported log samples for a representative period, with the source system clearly identified.",
    "screenshot": "Provide a dated screenshot from the production console showing the setting in effect.",
    "training": "Provide the training material plus completion records showing who completed it and when.",
    "contract": "Provide the signed agreement/contract clause that imposes the requirement.",
    "inventory": "Provide the current inventory or asset list, with its coverage and last-refresh date.",
    "evidence": "Provide any artefact from the live environment that demonstrates the control operates as described.",
}

# Words that carry no signal when matching a document request to a control.
_STOPWORDS = frozenset("""
about across against alway and any approved areasset been being both current
currently document documentation each evidence for from has have into its latest
must not organization organisation other over please provide provided recent
should show showing some that the their them there these this those with within
you your all and are for the was were will
""".split())


# A request word only counts as a control match if it names at most this many
# controls — i.e. it actually identifies something rather than describing it.
_DISTINCTIVE_MAX = 3


def _tokens(text: str | None) -> set[str]:
    """Lowercased word set used for keyword matching (stopwords/short words out)."""
    word = []
    out: set[str] = set()
    for ch in (text or "").lower():
        if ch.isalnum():
            word.append(ch)
        elif word:
            out.add("".join(word))
            word = []
    if word:
        out.add("".join(word))
    return {w for w in out if len(w) >= 3 and w not in _STOPWORDS and not w.isdigit()}


# Engagement-document buckets the Engagement Documents tab uploads into.
DOC_TYPES = ("survey", "transcript", "evidence", "policy")
DEFAULT_DOC_TYPE = "evidence"
# Manifest of bulk-uploaded engagement documents (doc_type per stored file),
# kept inside each assessment's evidence folder. Excluded from prefill/listing.
MANIFEST_NAME = "_engagement_manifest.json"


def _is_raw_evidence(fn: str) -> bool:
    """True for an actual uploaded evidence file (not a sidecar/manifest)."""
    return fn != MANIFEST_NAME and not fn.endswith(".preview.txt")


def _list_raw_evidence(ev_dir: str) -> list[str]:
    if not os.path.isdir(ev_dir):
        return []
    return [fn for fn in sorted(os.listdir(ev_dir)) if _is_raw_evidence(fn)]


# Root maturity labels (across NIST/ISO/CIS bands) → the frontend's 1-5 scale.
_MATURITY_INT = {
    "Optimizing": 5, "Managed": 4, "Defined": 3, "Developing": 2, "Initial": 1,
    "IG3": 5, "IG2": 4, "IG1 partial": 3, "Below IG1": 1,
    "Conformant": 5, "Minor NC": 4, "Major NC": 2, "Critical NC": 1,
    "Mature": 5, "Partial": 3, "Critical Gap": 1,
    # Organizational Assessment 0-3 ladder — previously absent, so these labels fell
    # through to the percentage fallback.
    "Strong": 5, "Passable": 4, "Partially in place": 2, "Not in place": 1,
}


def _pct_to_maturity(pct: float) -> int:
    if pct >= 80:
        return 5  # Low / Optimizing
    if pct >= 60:
        return 4  # Medium / Managed
    if pct >= 40:
        return 3  # High / Defined
    if pct >= 20:
        return 2  # Developing
    return 1      # Critical / Initial


def _maturity_to_int(label, pct: float) -> int:
    """Map a scorer maturity label to the UI's 1-5 scale, keeping it consistent
    with the score actually shown next to it.

    The label and the percentage come from different layers of the scorer and can
    disagree badly — the LLM path defaults ``maturity_level`` to "Defined" when it
    omits the field, which rendered a 92.6% assessment as maturity 3 and a 17.9%
    one as maturity 3 as well. The percentage is what the UI displays, so it wins
    whenever the label contradicts it; the label only refines the result when the
    two already agree to within one level.
    """
    from_pct = _pct_to_maturity(pct)
    from_label = _MATURITY_INT.get(str(label or "").strip())
    if from_label is None:
        return from_pct
    return from_label if abs(from_label - from_pct) <= 1 else from_pct


def _severity_from_score(score: float) -> str:
    if score < 25:
        return "critical"
    if score < 50:
        return "high"
    if score < 75:
        return "medium"
    return "low"


# Default generation config for assessments created before the builder exposed
# "Generation Settings" (generation_config is NULL). framework_only preserves
# the original deterministic behaviour — no LLM, framework questions only.
_GEN_STRATEGIES = ("framework_only", "framework_plus_ai", "ai_rewrite")


def _gen_cfg(assessment) -> dict:
    """Normalised generation config with safe fallbacks. A missing or unknown
    strategy falls back to framework_only so legacy/no-config assessments keep
    working exactly as before (the builder UI enforces an explicit choice)."""
    cfg = getattr(assessment, "generation_config", None) or {}
    strategy = (cfg.get("strategy") or "").strip()
    if strategy not in _GEN_STRATEGIES:
        strategy = "framework_only"
    return {
        "strategy": strategy,
        "depth": (cfg.get("depth") or "standard").strip().lower(),
        "evidence_policy": (cfg.get("evidence_policy") or "high_and_critical").strip().lower(),
        "custom_instructions": (cfg.get("custom_instructions") or "").strip(),
        "recommendation_mode": (cfg.get("recommendation_mode") or "hybrid").strip().lower(),
    }


def _domain_selected(fw_id: str, domain, selected: set) -> bool:
    """A domain is in scope if its composite key, code, or id was selected."""
    return (
        f"{fw_id}:{domain.code}" in selected
        or domain.code in selected
        or domain.id in selected
    )


# Canonical files store lowercase question types the frontend never matches
# (so everything rendered as a textarea). Normalise to the UI's uppercase set.
_QUESTION_TYPE_MAP = {
    "yes_no": "YES_NO", "yes_no_with_detail": "YES_NO",
    "maturity_rating": "SCALE_1_5", "scale_1_5": "SCALE_1_5", "scale": "SCALE_1_5",
    "multi_choice": "MULTI_CHOICE", "pre_assessment": "PRE_ASSESSMENT",
    "free_text": "FREE_TEXT", "interview": "FREE_TEXT",
    # No per-question upload input — evidence flows through document requests /
    # engagement documents; the question itself is answered in text.
    "evidence_upload": "FREE_TEXT",
}
_YES_NO_STARTS = (
    "is ", "are ", "do ", "does ", "has ", "have ", "was ", "were ",
    "did ", "can ", "will ", "should ",
)
_SCALE_HINTS = ("how mature", "to what extent", "how well", "how consistently",
                "rate ", "on a scale")


def _mixed_question_type(text: str, current: str) -> str:
    """Pick the input type that matches how the question is phrased."""
    cur = _QUESTION_TYPE_MAP.get(
        (current or "").strip().lower(), (current or "").strip().upper()
    )
    if cur in ("MULTI_CHOICE", "PRE_ASSESSMENT", "SCALE_1_5"):
        return cur
    t = (text or "").strip().lower()
    if any(h in t for h in _SCALE_HINTS):
        return "SCALE_1_5"
    if t.startswith(_YES_NO_STARTS):
        return "YES_NO"
    return "FREE_TEXT"


class AssessmentService:
    def __init__(self, db: AsyncSession):
        self.repo = AssessmentRepository(db)
        self.fw_repo = FrameworkRepository(db)
        self.scoring = ScoringService(db)
        # Per-request caches (the service is constructed per HTTP request).
        self._fw_cache: dict[str, object] = {}
        self._guide_cache: dict[str, dict] = {}
        self._contributor_scope_cache: dict[str, set[str]] = {}

    async def list_assessments(self) -> list[Assessment]:
        return await self.repo.get_all()

    # ── Caller scoping (access control) ─────────────────────────────────────
    #
    # Contributors (team members / evidence contributors) have one job: receive
    # document requests from reviewers and upload what was asked for. They must
    # therefore never see the whole catalogue — not even their own organization's
    # everything. Everyone else's visibility is unchanged, and an unidentified
    # caller (no X-User-Role header: tests, SSR, scripts) stays permissive,
    # matching the ceiling documented in backend/api/authz.py.

    async def _caller_market(self, caller) -> str | None:
        """The organization recorded against the caller's email in the users table.

        The legacy field is `organization` (the flat single-org model). Matching
        is by label, so older `market`-era rows that only existed pre-migration
        are not required here; `organization` is authoritative now.
        """
        email = (getattr(caller, "email", None) or "").strip().lower()
        if not email:
            return None
        result = await self.repo.db.execute(
            select(User.organization).where(User.email == email)
        )
        return result.scalars().first()

    async def contributor_assessment_ids(self, caller) -> set[str]:
        """Assessment ids a contributor is actually INVOLVED in.

        Involvement is evidenced by the document_requests table — organization
        membership alone is not enough, otherwise a contributor would see every
        assessment in their org including ones nobody has asked them about.
        An assessment qualifies when it carries at least one request AND either:
          * the contributor already fulfilled one of its requests (provided_by),
            or
          * it belongs to the contributor's organization, i.e. they are a
            designated contact the request is directed at.
        Returns an empty set when neither holds (no error — just no work).
        """
        email = (getattr(caller, "email", None) or "").strip().lower()
        cache_key = email or "\x00anonymous"
        if cache_key in self._contributor_scope_cache:
            return self._contributor_scope_cache[cache_key]

        market_key = _norm_market(await self._caller_market(caller))
        rows = await self.repo.db.execute(
            select(
                DocumentRequest.assessment_id,
                DocumentRequest.provided_by,
                Assessment.market_id,
                Assessment.market_label,
            ).join(Assessment, DocumentRequest.assessment_id == Assessment.id)
        )
        ids: set[str] = set()
        for assessment_id, provided_by, market_id, market_label in rows.all():
            if email and (provided_by or "").strip().lower() == email:
                ids.add(assessment_id)
            elif market_key and market_key in (
                _norm_market(market_id), _norm_market(market_label)
            ):
                ids.add(assessment_id)
        self._contributor_scope_cache[cache_key] = ids
        return ids

    async def list_for_caller(self, caller=None) -> list[Assessment]:
        """`GET /assessments`, narrowed to what the caller may see.

        Reviewers and unidentified callers get the full list exactly as before;
        contributors get only their involved subset.
        """
        assessments = await self.repo.get_all()
        if getattr(caller, "role", None) not in CONTRIBUTOR_ROLES:
            return assessments
        allowed = await self.contributor_assessment_ids(caller)
        return [a for a in assessments if a.id in allowed]

    async def caller_can_access(self, caller, assessment_id: str) -> bool:
        """Guard for per-assessment endpoints (detail, document requests…)."""
        if getattr(caller, "role", None) not in CONTRIBUTOR_ROLES:
            return True
        return assessment_id in await self.contributor_assessment_ids(caller)

    async def get_assessment(self, assessment_id: str) -> Assessment | None:
        return await self.repo.get_by_id(assessment_id)

    async def create_assessment(self, data: AssessmentCreate) -> Assessment:
        assessment = Assessment(
            name=data.name,
            description=data.description,
            framework_ids=data.framework_ids,
            selected_domains=data.selected_domains or [],
            organization=data.organization,
            assigned_to=((data.assigned_to or "").strip().lower() or None),
            generation_config=(
                data.generation_config.model_dump() if data.generation_config else None
            ),
            market_id=data.market_id or None,
            market_label=data.market_label or None,
            status="draft",
        )
        assessment = await self.repo.create(assessment)
        return assessment

    async def assign_assessment(self, assessment_id: str, assigned_to: str | None) -> Assessment:
        """Assign (or unassign) an assessment to an individual Assessor."""
        assessment = await self.repo.get_by_id(assessment_id)
        if not assessment:
            raise ValueError(f"Assessment {assessment_id} not found")
        await self.repo.update_assigned_to(assessment_id, (assigned_to or "").strip().lower() or None)
        return await self.repo.get_by_id(assessment_id)

    async def load_pre_assessment(
        self, assessment_id: str, file_name: str, file_content: bytes,
    ) -> dict:
        """Parse a custom Pre-Assessment questionnaire uploaded at creation and
        store its questions for this assessment (as a 'Pre-Assessment' section).

        If no custom file is uploaded the default questionnaire is used instead;
        this path only runs when the Central Admin provides one. Supports JSON
        (list of questions or {questions:[...]}) , CSV, and plain text / DOCX /
        PDF (one question per line)."""
        assessment = await self.repo.get_by_id(assessment_id)
        if not assessment:
            raise ValueError(f"Assessment {assessment_id} not found")

        # Persist the original file so owners can download it later.
        pre_folder = os.path.join(settings.PRE_ASSESSMENT_FOLDER, assessment_id)
        os.makedirs(pre_folder, exist_ok=True)
        if assessment.pre_assessment_file_path and os.path.exists(assessment.pre_assessment_file_path):
            try:
                os.remove(assessment.pre_assessment_file_path)
            except OSError:
                pass
        safe_name = f"{uuid.uuid4()}_{file_name}"
        saved_path = os.path.join(pre_folder, safe_name)
        with open(saved_path, "wb") as fh:
            fh.write(file_content)
        assessment.pre_assessment_file_name = file_name
        assessment.pre_assessment_file_path = saved_path
        await self.repo.db.flush()

        questions = self._parse_pre_assessment_file(file_name, file_content)
        if not questions:
            return {"ok": False, "error": "No questions could be parsed from the file."}

        # Replace any previous pre-assessment questions for this assessment.
        old_ids = {
            f.id for f in (assessment.followups or [])
            if getattr(f, "source", "ai") == "pre_assessment"
        }
        if old_ids:
            await self.repo.db.execute(
                sql_delete(FollowupQuestion).where(FollowupQuestion.id.in_(old_ids))
            )
            await self.repo.db.flush()

        created: list[FollowupQuestion] = []
        for idx, q in enumerate(questions):
            fu = FollowupQuestion(
                assessment_id=assessment_id,
                framework_code=None,
                domain_code="PRE", domain_name="Pre-Assessment",
                category_code="PRE", category_name="Pre-Assessment",
                control_code=None,
                text=q["text"],
                question_type=q.get("question_type") or "FREE_TEXT",
                weight=float(q.get("weight", 2.0) or 2.0),
                order_index=idx,
                source="pre_assessment",
            )
            self.repo.db.add(fu)
            created.append(fu)
        await self.repo.db.flush()

        # Keep the questionnaire in sync if it already exists (drop the previous
        # pre-assessment ids, append the new ones). Otherwise generate_questionnaire
        # will pick them up when the assessment is first opened.
        questionnaire = await self.repo.get_questionnaire(assessment_id)
        if questionnaire:
            base = [qid for qid in (questionnaire.question_ids or []) if qid not in old_ids]
            questionnaire.question_ids = base + [f.id for f in created]
            questionnaire.total_questions = len(questionnaire.question_ids)
            await self.repo.db.flush()

        return {"ok": True, "count": len(created)}

    @staticmethod
    def _parse_pre_assessment_file(file_name: str, content: bytes) -> list[dict]:
        """Extract a list of {text, question_type} from an uploaded questionnaire."""
        ext = os.path.splitext(file_name or "")[1].lower()

        # 1) JSON — a list of questions, or {"questions": [...]}.
        if ext == ".json":
            try:
                data = json.loads(content.decode("utf-8", errors="ignore"))
            except Exception:
                data = None
            items = data.get("questions") if isinstance(data, dict) else data
            out: list[dict] = []
            for it in (items or []):
                if isinstance(it, str):
                    text = it.strip()
                    qtype = None
                elif isinstance(it, dict):
                    text = str(it.get("text") or it.get("question") or "").strip()
                    qtype = it.get("question_type") or it.get("type")
                    qtype = _QUESTION_TYPE_MAP.get(str(qtype).strip().lower()) if qtype else None
                else:
                    continue
                if text:
                    out.append({"text": text,
                                "question_type": qtype or _mixed_question_type(text, "")})
            if out:
                return out

        # 2) CSV — proper parsing: detect a "Question" column by header name,
        #    fall back to the widest/most question-like column, then single column.
        if ext == ".csv":
            import csv as _csv, io as _io, re
            text = content.decode("utf-8-sig", errors="ignore")  # strips BOM
            reader = list(_csv.reader(_io.StringIO(text)))
            if not reader:
                return []

            # Find a column whose header looks like "question" / "text" / "survey"
            _Q_HEADERS = {"question", "questions", "text", "survey question",
                          "question text", "item", "prompt"}
            header = [c.strip().lower() for c in reader[0]]
            q_col = next((i for i, h in enumerate(header) if h in _Q_HEADERS), None)

            # No named header found — skip the first row if it looks like a header
            # (all cells are short non-question strings), else include it.
            data_rows = reader[1:] if q_col is not None or all(
                len(c.strip()) < 60 and "?" not in c for c in reader[0]
            ) else reader

            if q_col is None:
                # Pick the column with the most question-like content (contains "?"
                # or is the longest on average), defaulting to col 1 for the
                # standard template (Section, Question, …) or col 0 for single-col.
                if reader and len(reader[0]) > 1:
                    col_scores = [0] * len(reader[0])
                    for row in data_rows:
                        for i, cell in enumerate(row):
                            if "?" in cell:
                                col_scores[i] += 2
                            elif len(cell.strip()) > 20:
                                col_scores[i] += 1
                    q_col = col_scores.index(max(col_scores))
                else:
                    q_col = 0

            out = []
            for row in data_rows:
                if q_col >= len(row):
                    continue
                line = row[q_col].strip()
                line = re.sub(r"^\s*(\d+[.)]|[-*•])\s*", "", line).strip()
                if not line or len(line) < 4:
                    continue
                if line.lower() in ("question", "questions", "text", "section"):
                    continue
                out.append({"text": line, "question_type": _mixed_question_type(line, "")})
            return out

        # 3) Everything else → extract text and treat each non-empty line as a
        #    question. Reuses the evidence extractor for DOCX/PDF/XLSX.
        text_blob = ""
        if ext in (".txt", ".md"):
            text_blob = content.decode("utf-8", errors="ignore")
        else:
            # Persist to a temp file so the extractor (which reads paths) can run.
            import tempfile
            from backend.api.services.prefill import extract_evidence_text
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=ext or ".bin") as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                text_blob = extract_evidence_text(tmp_path) or ""
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

        out = []
        import re
        for raw in text_blob.splitlines():
            line = raw.strip()
            if not line or line.lower() in ("question", "questions", "text"):
                continue
            if len(line) < 4:
                continue
            # Strip a leading "1." / "1)" / "-" enumerator.
            line = re.sub(r"^\s*(\d+[.)]|[-*•])\s*", "", line).strip()
            if line:
                out.append({"text": line, "question_type": _mixed_question_type(line, "")})
        return out

    async def delete_assessment(self, assessment_id: str) -> bool:
        """Remove an assessment, its DB children, and its evidence files on disk."""
        deleted = await self.repo.delete(assessment_id)
        if not deleted:
            return False
        ev_dir = os.path.join(settings.EVIDENCE_FOLDER, assessment_id)
        if os.path.isdir(ev_dir):
            import shutil
            shutil.rmtree(ev_dir, ignore_errors=True)
        return True

    async def generate_questionnaire(
        self, assessment_id: str, generation_config: dict | None = None
    ) -> Questionnaire:
        assessment = await self.repo.get_by_id(assessment_id)
        if not assessment:
            raise ValueError(f"Assessment {assessment_id} not found")

        # An explicit override (sent with the generate call) is persisted so the
        # assessment remembers how it was generated and re-generation is stable.
        if generation_config is not None:
            assessment.generation_config = generation_config
            await self.repo.db.flush()

        # Author maturity-diagnostic questions for in-scope domains (best-effort;
        # falls back to existing control questions if no LLM / on failure). The
        # generation strategy (framework_only|framework_plus_ai|ai_rewrite) is
        # read from the assessment's generation_config.
        await self._ensure_diagnostic_questions(assessment)
        # Diagnostic generation mutates question rows; drop stale identity-map
        # state so the collection below sees the freshly written questions.
        self.repo.db.expire_all()
        # expire_all() also expired `assessment` — reload it explicitly (an
        # expired attribute access would lazy-load synchronously and raise
        # MissingGreenlet under the async session).
        assessment = await self.repo.get_by_id(assessment_id)

        from backend.api.models.framework import Question

        cfg = _gen_cfg(assessment)
        strategy = cfg["strategy"]
        selected = set(assessment.selected_domains or [])

        # For AI strategies, build a per-domain index of diagnostic FollowupQuestion
        # IDs generated by _ensure_diagnostic_questions above.
        diag_by_domain: dict[str, list[str]] = {}
        if strategy in ("framework_plus_ai", "ai_rewrite"):
            domain_order: dict[str, list[tuple[int, str]]] = {}
            for f in (assessment.followups or []):
                if getattr(f, "source", None) == "diagnostic" and f.domain_code:
                    domain_order.setdefault(f.domain_code, []).append(
                        (f.order_index, f.id)
                    )
            for k, pairs in domain_order.items():
                diag_by_domain[k] = [fid for _, fid in sorted(pairs)]

        question_ids = []
        for fw_id in assessment.framework_ids:
            fw = await self.fw_repo.get_by_id(fw_id)
            if not fw:
                continue
            for d in fw.domains:
                if selected and not _domain_selected(fw_id, d, selected):
                    continue

                diag_ids = diag_by_domain.get(d.code, [])

                # ai_rewrite: when diagnostics exist for this domain, use them
                # exclusively — framework questions are skipped entirely (but
                # not deleted, so other assessments remain unaffected).
                if strategy == "ai_rewrite" and diag_ids:
                    question_ids.extend(diag_ids)
                    continue

                for c in d.controls:
                    # Mix question types: retype each question to match its
                    # phrasing (also normalises canonical lowercase types).
                    types_seen = set()
                    for q in c.questions:
                        new_type = _mixed_question_type(q.text, q.question_type)
                        if new_type != q.question_type:
                            q.question_type = new_type
                        types_seen.add(new_type)
                    question_ids.extend([q.id for q in c.questions])
                    # An all-yes/no control also gets one maturity-scale
                    # question so the type mix is guaranteed per control.
                    if len(c.questions) >= 2 and types_seen == {"YES_NO"}:
                        scale_q = Question(
                            control_id=c.id,
                            framework_id=fw.id,
                            text=("How mature is this control in practice? "
                                  "(1 = not in place, 5 = fully implemented, "
                                  "monitored and regularly reviewed)"),
                            help_text=c.statement,
                            question_type="SCALE_1_5",
                            weight=3.0,
                            order_index=len(c.questions),
                        )
                        self.repo.db.add(scale_q)
                        await self.repo.db.flush()
                        question_ids.append(scale_q.id)

                # framework_plus_ai: append domain diagnostics after the
                # framework questions for the same domain.
                if strategy == "framework_plus_ai" and diag_ids:
                    question_ids.extend(diag_ids)

        # Preserve any custom pre-assessment questions (uploaded by the Central
        # Admin at creation) so a regenerated questionnaire still includes them.
        pre_ids = [
            f.id for f in sorted(
                (assessment.followups or []), key=lambda x: x.order_index
            )
            if getattr(f, "source", "ai") == "pre_assessment"
        ]
        question_ids.extend(pre_ids)

        await self.repo.db.execute(
            sql_delete(Questionnaire).where(Questionnaire.assessment_id == assessment_id)
        )
        await self.repo.db.flush()

        questionnaire = Questionnaire(
            assessment_id=assessment_id,
            question_ids=question_ids,
            total_questions=len(question_ids),
            answered_count=0,
        )
        q = await self.repo.create_questionnaire(questionnaire)
        await self.repo.update_status(assessment_id, "in_progress")
        return q

    async def submit_assessment(self, assessment_id: str) -> dict:
        """Submit a completed-by-the-respondent assessment for review.

        Used by roles that can answer but not score (e.g. owner). Moves the
        assessment to 'in_review' so an assessor can score it."""
        a = await self.repo.get_by_id(assessment_id)
        if not a:
            raise ValueError(f"Assessment {assessment_id} not found")
        await self.repo.update_status(assessment_id, "in_review")
        return {"id": assessment_id, "status": "in_review"}

    async def _ensure_diagnostic_questions(self, assessment) -> None:
        """Generate & persist maturity-diagnostic questions per-assessment into
        followup_questions (source='diagnostic').  Never writes to the shared
        framework questions table, so one assessment cannot corrupt another.

        Idempotent per (assessment, domain): skips any domain whose diagnostics
        already exist for this assessment. Best-effort: skips on missing API key
        and leaves a domain untouched on generation failure.

        Driven by the assessment's generation_config.strategy:
          - framework_only    : no LLM — use imported framework questions as-is.
          - framework_plus_ai : keep framework questions AND append diagnostics.
          - ai_rewrite        : diagnostic questions replace framework questions
                            in the questionnaire (framework rows are never
                            deleted — they stay intact for other assessments).
        """
        cfg = _gen_cfg(assessment)
        strategy = cfg["strategy"]
        if strategy == "framework_only":
            return

        api_key = os.getenv("GROQ_API_KEY", "")
        if not api_key:
            return
        model = os.getenv("GROQ_MODEL", "") or "openai/gpt-oss-120b"
        count = 10 if cfg["depth"] == "detailed" else 6
        extra_instructions = cfg["custom_instructions"]
        tpm_limit = int(os.getenv("PREFILL_TPM_LIMIT", "5500"))

        import asyncio
        import time
        from backend.services.questionnaire import QuestionnaireBuilder

        builder = QuestionnaireBuilder(api_key, model)
        selected = set(assessment.selected_domains or [])
        sent: list[tuple[float, int]] = []

        # Per-assessment idempotency: track which domains already have
        # diagnostic FollowupQuestions so re-generation is a no-op.
        existing_diag_domains: set[str] = {
            f.domain_code
            for f in (assessment.followups or [])
            if getattr(f, "source", None) == "diagnostic" and f.domain_code
        }

        async def _wait(tokens: int) -> None:
            while True:
                now = time.monotonic()
                while sent and now - sent[0][0] > 60:
                    sent.pop(0)
                if not sent or sum(t for _, t in sent) + tokens <= tpm_limit:
                    return
                await asyncio.sleep(max(0.5, 60 - (now - sent[0][0]) + 0.5))

        for fw_id in assessment.framework_ids:
            fw = await self.fw_repo.get_by_id(fw_id)
            if not fw:
                continue
            for d in fw.domains:
                if selected and not _domain_selected(fw_id, d, selected):
                    continue
                controls = list(d.controls)
                if not controls:
                    continue
                # Already generated for this assessment — skip.
                if d.code in existing_diag_domains:
                    continue

                def _guide_for(c):
                    mc = c.maturity_criteria if isinstance(c.maturity_criteria, dict) else {}
                    inner = mc.get("maturity_guide")
                    guide = inner if isinstance(inner, dict) else mc
                    evidence = mc.get("expected_evidence_types") or []
                    return {
                        "control_id": c.code,
                        "sub_topic": c.category_name or c.name or c.code,
                        "control_statement": (c.statement or c.name or c.code or "")[:240],
                        "maturity_guide": guide,
                        "expected_evidence_types": list(evidence)[:6],
                        "sample_questions": [],
                    }

                guides = [_guide_for(c) for c in controls[:20]]
                guides_chars = sum(
                    len(g["control_statement"]) + len(g["sub_topic"]) + 60
                    for g in guides
                )
                est = guides_chars // 4 + 600 + 4096
                await _wait(est)
                sent.append((time.monotonic(), est))
                try:
                    qs = await asyncio.to_thread(
                        builder.generate_diagnostic_questions_for_domain,
                        d.name or d.code, guides, "", fw.name, count,
                        extra_instructions,
                    )
                except Exception as exc:
                    from backend.core.utils import logger
                    logger.warning(
                        "Diagnostic generation skipped for %s: %s", d.code, exc
                    )
                    continue
                if not qs:
                    continue

                by_code = {c.code: c for c in controls}
                for idx, q in enumerate(qs):
                    ctrl = by_code.get(q.get("control_id")) or controls[0]
                    self.repo.db.add(FollowupQuestion(
                        assessment_id=assessment.id,
                        framework_code=fw.code,
                        domain_code=d.code,
                        domain_name=d.name,
                        category_code=getattr(ctrl, "category_code", None) or ctrl.code,
                        category_name=ctrl.category_name or ctrl.name,
                        control_code=ctrl.code,
                        text=q["text"],
                        help_text=ctrl.statement,
                        question_type=q.get("question_type", "FREE_TEXT"),
                        choices=q.get("choices"),
                        weight=float(q.get("weight", 3) or 3),
                        expected_evidence_types=q.get("expected_evidence_types") or [],
                        sub_topic=q.get("sub_topic"),
                        maturity_signals=q.get("maturity_signals"),
                        gap_if_deficient=q.get("gap_if_deficient"),
                        order_index=idx,
                        source="diagnostic",
                    ))
                await self.repo.db.flush()

    async def save_response(self, assessment_id: str, data: ResponseCreate) -> Response:
        q_type = "YES_NO"
        if data.question_id:
            q = await self.fw_repo.get_question_by_id(data.question_id)
            if q:
                q_type = q.question_type
            else:
                # May be a diagnostic FollowupQuestion (source='diagnostic')
                fq = await self.repo.get_followup_by_id(data.question_id)
                if fq:
                    q_type = fq.question_type
        score = score_response(data.response_value, q_type)

        response = Response(
            assessment_id=assessment_id,
            question_id=data.question_id,
            control_id=data.control_id,
            response_value=data.response_value,
            notes=data.notes,
            score=score,
        )
        saved = await self.repo.upsert_response(response)

        questionnaire = await self.repo.get_questionnaire(assessment_id)
        if questionnaire:
            responses = await self.repo.get_responses(assessment_id)
            questionnaire.answered_count = sum(1 for r in responses if r.response_value is not None)
        return saved

    async def upload_evidence(
        self, assessment_id: str, response_id: str, file_name: str, file_content: bytes
    ) -> Evidence:
        evidence_dir = os.path.join(settings.EVIDENCE_FOLDER, assessment_id)
        os.makedirs(evidence_dir, exist_ok=True)

        safe_name = f"{uuid.uuid4()}_{_safe_basename(file_name)}"
        file_path = os.path.join(evidence_dir, safe_name)
        with open(file_path, "wb") as f:
            f.write(file_content)

        # Extract a text preview and persist a sidecar .txt so the prefill step
        # can read it; store a short preview on the row.
        from backend.api.services.prefill import extract_evidence_text
        preview = extract_evidence_text(file_path) or ""
        if preview:
            try:
                with open(file_path + ".preview.txt", "w", encoding="utf-8") as fh:
                    fh.write(preview)
            except Exception:
                pass

        evidence = Evidence(
            response_id=response_id,
            assessment_id=assessment_id,
            file_name=file_name,
            file_path=file_path,
            file_type=os.path.splitext(file_name)[1].lstrip(".").lower(),
            file_size=len(file_content),
            description=preview[:2000] or None,
        )
        return await self.repo.add_evidence(evidence)

    async def _control_context(self, assessment: Assessment):
        """Build {control_code: Control} and the framework key for scoring."""
        control_map: dict = {}
        framework_key = "nist-csf-2-0"
        first = True
        for fw_id in assessment.framework_ids:
            fw = await self.fw_repo.get_by_id(fw_id)
            if fw and first:
                framework_key = _framework_key(fw.code)
                first = False
            for c in await self.fw_repo.get_controls_for_framework(fw_id):
                control_map[c.code] = c
        return control_map, framework_key

    async def _persist_scoring(self, assessment_id: str, scoring: dict, complete: bool) -> dict:
        """Persist a root-scorer result (overall, maturity, per-domain scores,
        findings) so every surface reads the SAME numbers. Shared by
        score_and_finalize (complete=True) and the report view (complete=False)."""
        reviews = scoring.get("question_reviews", [])
        overall_pct = round(float(scoring.get("control_score_percent") or 0.0), 1)
        maturity_int = _maturity_to_int(scoring.get("maturity_level"), overall_pct)
        framework_code = scoring.get("framework_key") or ""

        is_market = framework_code == "market-assessment"
        by_domain: dict[str, list[dict]] = {}
        for r in reviews:
            d = str(r.get("domain_id") or "").split("-")[0] or "—"
            by_domain.setdefault(d, []).append(r)

        scores: list[Score] = [Score(
            assessment_id=assessment_id, level="framework",
            framework_code=framework_code,
            percentage=overall_pct, maturity_level=maturity_int,
            answered_questions=sum(1 for r in reviews if r.get("verdict") != "missing"),
            total_questions=len(reviews),
        )]
        ai_levels = scoring.get("ai_item_levels") or {}
        for d, drevs in by_domain.items():
            if is_market:
                # Same achieved/possible roll-up the report shows, so the two match.
                d_pct = market_control_rollup(drevs, ai_levels=ai_levels)["pct"]
            else:
                vals = [float(r.get("score") or 0) for r in drevs]
                d_pct = round(sum(vals) / len(vals), 1) if vals else 0.0
            answered = sum(1 for r in drevs if str(r.get("response") or "").strip())
            scores.append(Score(
                assessment_id=assessment_id, level="domain", domain_code=d,
                percentage=d_pct, maturity_level=_pct_to_maturity(d_pct),
                answered_questions=answered, total_questions=len(drevs),
            ))

        findings: list[Finding] = []
        for r in reviews:
            score = float(r.get("score") or 0)
            verdict = str(r.get("verdict") or "").lower()
            if verdict not in ("weak", "missing") and score >= 50:
                continue
            ctrl = r.get("control_id") or ""
            gap = (r.get("gap") or r.get("comment") or r.get("question_text") or "").strip()
            findings.append(Finding(
                assessment_id=assessment_id,
                control_id=ctrl, control_code=ctrl,
                framework_code=framework_code,
                domain_code=str(r.get("domain_id") or "").split("-")[0],
                title=f"Gap: {(r.get('question_text') or ctrl or 'control')[:80]}",
                gap_description=gap[:500] or None,
                recommendation=(
                    f"Address {ctrl or 'this control'}: provide and document the "
                    f"expected control activity and retain supporting evidence."
                ),
                severity=_severity_from_score(score),
                status="open",
            ))

        await self.repo.delete_findings(assessment_id)
        if findings:
            await self.repo.create_findings(findings)
        await self.repo.replace_scores(assessment_id, scores)
        await self.repo.update_scores(assessment_id, overall_pct, maturity_int)
        if complete:
            await self.repo.update_status(assessment_id, "completed")

        return {
            "overall_score": overall_pct,
            "maturity_level": maturity_int,
            "domain_scores": [
                {"domain": s.domain_code, "domain_code": s.domain_code,
                 "percentage": s.percentage, "maturity": s.maturity_level}
                for s in scores if s.level == "domain"
            ],
            "findings_count": len(findings),
        }

    async def score_and_finalize(self, assessment_id: str) -> dict:
        """Score the assessment with the root scorer and mark it completed."""
        import asyncio
        from backend.api.services.report_builder import compute_scoring
        assessment = await self.repo.get_by_id(assessment_id)
        if not assessment:
            raise ValueError(f"Assessment {assessment_id} not found")
        if assessment.status != "in_review":
            raise ValueError("Assessment must be submitted before it can be scored.")
        question_map = await self._build_question_map(assessment)
        # Run in thread pool — compute_scoring may invoke the LLM (blocking I/O).
        scoring = await asyncio.to_thread(compute_scoring, assessment, question_map)
        return await self._persist_scoring(assessment_id, scoring, complete=True)

    async def prefill_answers(self, assessment_id: str) -> dict:
        """Draft answers from uploaded evidence using the ROOT prefill pipeline."""
        assessment = await self.repo.get_by_id(assessment_id)
        if not assessment:
            raise ValueError(f"Assessment {assessment_id} not found")

        # Gather evidence text for this assessment (prefers cached
        # .preview.txt sidecars, else extracts live and caches).
        sections, uploaded_count = self._gather_evidence_sections(assessment_id)
        if not sections:
            msg = ("Uploaded evidence has no extractable text (e.g. scanned images "
                   "or unsupported file types). Upload a text-based PDF/DOCX/TXT.") \
                  if uploaded_count else "No evidence uploaded yet."
            return {"ok": True, "answers": {},
                    "summary": {"filled": 0, "total": 0, "message": msg}}

        # Answerable questions (skip evidence-upload type), scoped to the
        # selected domains so we only draft answers for in-scope controls.
        selected = set(assessment.selected_domains or [])
        questions: list[dict] = []
        by_id: dict[str, dict] = {}
        for fw_id in assessment.framework_ids:
            fw = await self.fw_repo.get_by_id(fw_id)
            if not fw:
                continue
            for d in fw.domains:
                if selected and not _domain_selected(fw_id, d, selected):
                    continue
                for c in d.controls:
                    for q in c.questions:
                        if str(q.question_type).upper() in ("EVIDENCE_UPLOAD", "EVIDENCE"):
                            continue
                        meta = {"question_id": q.id, "question_type": q.question_type,
                                "question_text": q.text, "text": q.text,
                                "choices": q.choices, "control_id": c.code}
                        questions.append(meta)
                        by_id[q.id] = meta
        if not questions:
            return {"ok": True, "answers": {}, "summary": {"filled": 0, "total": 0}}

        api_key = os.getenv("GROQ_API_KEY", "")
        if not api_key:
            return {"ok": False, "error": "GROQ_API_KEY is not configured."}
        model = os.getenv("GROQ_MODEL", "") or "openai/gpt-oss-120b"

        import asyncio
        import time
        from backend.api.services.prefill import (
            select_evidence_for_batch, prefill_with_llm, map_prefill_answer,
        )

        # Free Groq tiers have low token-per-minute (TPM) limits, so keep each
        # request small (relevant evidence only) and throttle to stay under the
        # limit instead of hammering it into 413/429 errors.
        BATCH = int(os.getenv("PREFILL_BATCH_SIZE", "5"))
        MAX_TOKENS = int(os.getenv("PREFILL_MAX_TOKENS", "900"))
        TPM_LIMIT = int(os.getenv("PREFILL_TPM_LIMIT", "5500"))

        def _run() -> dict:
            result: dict = {}
            errors: list[str] = []
            sent: list[tuple[float, int]] = []  # (timestamp, est_tokens) in last 60s

            def _wait_for_budget(tokens: int) -> None:
                """Block until sending `tokens` keeps the rolling 60s usage under
                TPM_LIMIT (token-bucket throttle)."""
                while True:
                    now = time.monotonic()
                    while sent and now - sent[0][0] > 60:
                        sent.pop(0)
                    used = sum(t for _, t in sent)
                    if not sent or used + tokens <= TPM_LIMIT:
                        return
                    sleep_for = 60 - (now - sent[0][0]) + 0.5
                    time.sleep(max(0.5, sleep_for))

            for i in range(0, len(questions), BATCH):
                batch = questions[i:i + BATCH]
                corpus = select_evidence_for_batch(sections, batch)
                est = len(corpus) // 4 + 400 + MAX_TOKENS  # rough token estimate
                _wait_for_budget(est)
                sent.append((time.monotonic(), est))
                try:
                    answers = prefill_with_llm(batch, corpus, api_key, model, max_tokens=MAX_TOKENS)
                except Exception as exc:  # one rate-limited batch shouldn't sink the rest
                    errors.append(str(exc))
                    continue
                for a in answers:
                    qid = a.get("question_id")
                    meta = by_id.get(qid)
                    if not meta:
                        continue
                    mapped = map_prefill_answer(meta, a)
                    if mapped and mapped.get("response"):
                        result[qid] = mapped
            # If every batch failed, surface the reason instead of "0 filled".
            if not result and errors:
                raise RuntimeError(errors[-1])
            return result

        try:
            drafted = await asyncio.to_thread(_run)
        except Exception as exc:
            return {"ok": False, "error": f"Pre-fill failed: {exc}"}

        # Auto-save the drafted answers as responses.
        saved = 0
        for qid, mp in drafted.items():
            meta = by_id.get(qid, {})
            sc = score_response(mp.get("response"), meta.get("question_type", "YES_NO"))
            resp = Response(
                assessment_id=assessment_id, question_id=qid,
                control_id=meta.get("control_id"),
                response_value=mp.get("response"), notes=mp.get("note"), score=sc,
            )
            await self.repo.upsert_response(resp)
            saved += 1

        return {"ok": True, "answers": drafted,
                "summary": {"filled": saved, "total": len(questions),
                            "evidence_files": [s[0] for s in sections]}}

    def _gather_evidence_sections(
        self, assessment_id: str,
    ) -> tuple[list[tuple[str, str]], int]:
        """Collect (filename, text) for every engagement document with extractable
        text. Prefers the cached .preview.txt sidecar, else extracts live."""
        from backend.api.services.prefill import extract_evidence_text
        ev_dir = os.path.join(settings.EVIDENCE_FOLDER, assessment_id)
        sections: list[tuple[str, str]] = []
        raw_files = _list_raw_evidence(ev_dir)
        for fn in raw_files:
            fp = os.path.join(ev_dir, fn)
            sidecar = fp + ".preview.txt"
            text = ""
            if os.path.exists(sidecar):
                try:
                    with open(sidecar, encoding="utf-8") as fh:
                        text = fh.read()
                except Exception:
                    text = ""
            if not text:
                text = extract_evidence_text(fp) or ""
                if text:
                    try:
                        with open(sidecar, "w", encoding="utf-8") as fh:
                            fh.write(text)
                    except Exception:
                        pass
            if text.strip():
                sections.append((fn, text))
        return sections, len(raw_files)

    async def rate_and_generate(self, assessment_id: str) -> dict:
        """Rate the uploaded engagement documents for each in-scope category against
        its criteria statement, and generate follow-up questions per category
        (appended to the Conduct questionnaire). Reuses the root LLM client."""
        assessment = await self.repo.get_by_id(assessment_id)
        if not assessment:
            raise ValueError(f"Assessment {assessment_id} not found")

        sections, uploaded_count = self._gather_evidence_sections(assessment_id)
        if not sections:
            msg = ("Uploaded documents have no extractable text (e.g. scanned images "
                   "or unsupported types).") if uploaded_count else \
                  "No engagement documents uploaded yet."
            return {"ok": True, "ratings": [], "generated": 0,
                    "summary": {"message": msg}}

        api_key = os.getenv("GROQ_API_KEY", "")
        if not api_key:
            return {"ok": False, "error": "GROQ_API_KEY is not configured."}
        model = os.getenv("GROQ_MODEL", "") or "openai/gpt-oss-120b"

        # Build a map of control_code → set of evidence filenames that were
        # explicitly uploaded for responses answering that control. These files
        # are prioritised first in each category's corpus so per-control evidence
        # is included regardless of keyword relevance rank (evidence sharing fix).
        ctrl_evidence_files: dict[str, set[str]] = {}
        for resp in (assessment.responses or []):
            ctrl_id = resp.control_id
            if not ctrl_id:
                continue
            for ev in (resp.evidence or []):
                safe_name = os.path.basename(ev.file_path)
                ctrl_evidence_files.setdefault(ctrl_id, set()).add(safe_name)

        # Build the in-scope category contexts.
        selected = set(assessment.selected_domains or [])
        cats: list[dict] = []
        for fw_id in assessment.framework_ids:
            fw = await self.fw_repo.get_by_id(fw_id)
            if not fw:
                continue
            for d in fw.domains:
                if selected and not _domain_selected(fw_id, d, selected):
                    continue
                # Frameworks may organise controls under explicit categories
                # (sub-domains) OR hang them directly off the domain. When a
                # domain has no categories, treat the domain itself as a single
                # category so its controls are still rated — mirrors the Conduct
                # tab's question builder, which applies the same fallback.
                categories = list(d.categories)
                if categories:
                    cat_specs = [
                        (cat.code, cat.name or cat.code, cat.criteria_statement,
                         list(cat.controls))
                        for cat in categories
                    ]
                else:
                    cat_specs = [(d.code, d.name or d.code,
                                  getattr(d, "criteria_statement", None),
                                  list(d.controls))]
                for cat_code, cat_name, criteria, controls in cat_specs:
                    if not controls:
                        continue
                    expected: list[str] = []
                    statements: list[str] = []
                    items: list[dict] = []
                    for c in controls:
                        if c.statement:
                            statements.append(c.statement)
                        guide = c.maturity_criteria if isinstance(c.maturity_criteria, dict) else {}
                        # Per-question expected evidence, plus any folded into the
                        # guide at ingestion (older imports may lack the latter).
                        c_expected: list[str] = list(guide.get("expected_evidence_types") or [])
                        for q in (c.questions or []):
                            c_expected.extend(q.expected_evidence_types or [])
                        expected.extend(c_expected)
                        items.append({
                            "control_code": c.code,
                            "name": c.name,
                            "statement": c.statement,
                            "item_type": guide.get("item_type") or guide.get("type"),
                            "scope_cadence": guide.get("scope_cadence"),
                            "preferred_tooling": guide.get("preferred_tooling"),
                            "expected_evidence_types": sorted(set(c_expected)),
                            "maturity_guide": guide,
                        })
                    cats.append({
                        "framework_code": fw.code,
                        "domain_code": d.code,
                        "domain_name": d.name,
                        "category_code": cat_code,
                        "category_name": cat_name,
                        "criteria_statement": criteria,
                        "expected_evidence_types": sorted(set(expected)),
                        "control_statements": statements,
                        "items": items,
                        "control_code": controls[-1].code,
                    })
        if not cats:
            return {"ok": True, "ratings": [], "generated": 0,
                    "summary": {"message": "No categories in scope."}}

        import asyncio
        import time
        from backend.api.services.prefill import select_evidence_for_batch
        from backend.api.services.ai_rating import rate_category, MAX_FOLLOWUPS

        TPM_LIMIT = int(os.getenv("PREFILL_TPM_LIMIT", "5500"))
        MAX_TOKENS = int(os.getenv("RATING_MAX_TOKENS", "1500"))

        def _run() -> list[dict]:
            out: list[dict] = []
            sent: list[tuple[float, int]] = []

            def _wait(tokens: int) -> None:
                while True:
                    now = time.monotonic()
                    while sent and now - sent[0][0] > 60:
                        sent.pop(0)
                    if not sent or sum(t for _, t in sent) + tokens <= TPM_LIMIT:
                        return
                    time.sleep(max(0.5, 60 - (now - sent[0][0]) + 0.5))

            for cat in cats:
                # Pull only the evidence most relevant to this category.
                pseudo_q = [{
                    "question_text": " ".join([
                        cat.get("category_name") or "", cat.get("criteria_statement") or "",
                        " ".join(cat.get("expected_evidence_types") or []),
                    ]),
                    "control_id": cat.get("category_code"),
                    "domain": cat.get("domain_name"),
                }]
                # Prepend files explicitly linked to this category's controls so
                # they appear first regardless of keyword relevance rank.
                priority_names: set[str] = set()
                for item in cat.get("items", []):
                    priority_names.update(
                        ctrl_evidence_files.get(item.get("control_code", ""), set())
                    )
                if priority_names:
                    ordered = (
                        [(fn, txt) for fn, txt in sections if fn in priority_names]
                        + [(fn, txt) for fn, txt in sections if fn not in priority_names]
                    )
                else:
                    ordered = sections
                corpus = select_evidence_for_batch(ordered, pseudo_q,
                                                   total_cap=5000, per_file_cap=2500)
                est = len(corpus) // 4 + 500 + MAX_TOKENS
                _wait(est)
                sent.append((time.monotonic(), est))
                try:
                    rating = rate_category(cat, corpus, api_key, model, max_tokens=MAX_TOKENS)
                except Exception as exc:
                    rating = {"error": str(exc)}
                out.append({"cat": cat, "rating": rating})
            return out

        try:
            results = await asyncio.to_thread(_run)
        except Exception as exc:
            return {"ok": False, "error": f"Rating failed: {exc}"}

        # Persist: replace prior ratings + AI follow-up questions for this
        # assessment. Only "ai" follow-ups are cleared — a custom pre-assessment
        # questionnaire (source="pre_assessment") is preserved across re-rating.
        old_fu_ids = {
            f.id for f in assessment.followups
            if getattr(f, "source", "ai") == "ai"
        }
        cr_del = sql_delete(CategoryRating).where(CategoryRating.assessment_id == assessment_id)
        fu_del = sql_delete(FollowupQuestion).where(
            FollowupQuestion.assessment_id == assessment_id,
            FollowupQuestion.source == "ai",
        )
        await self.repo.db.execute(cr_del)
        await self.repo.db.execute(fu_del)
        await self.repo.db.flush()

        ev_names = [s[0] for s in sections]
        ratings_out: list[dict] = []
        new_followups: list[FollowupQuestion] = []
        generated = 0
        errors: list[str] = []
        for item in results:
            cat = item["cat"]
            r = item["rating"]
            if r.get("error"):
                errors.append(r["error"])
                continue
            self.repo.db.add(CategoryRating(
                assessment_id=assessment_id,
                framework_code=cat["framework_code"],
                domain_code=cat["domain_code"], domain_name=cat["domain_name"],
                category_code=cat["category_code"], category_name=cat["category_name"],
                criteria_statement=cat["criteria_statement"],
                score=r["score"], verdict=r["verdict"], rationale=r["rationale"],
                missing=r["missing"], evidence_files=ev_names,
                item_ratings=r.get("items") or [],
            ))
            for idx, qtext in enumerate(r["followups"]):
                fu = FollowupQuestion(
                    assessment_id=assessment_id,
                    framework_code=cat["framework_code"],
                    domain_code=cat["domain_code"], domain_name=cat["domain_name"],
                    category_code=cat["category_code"], category_name=cat["category_name"],
                    control_code=cat["control_code"],
                    text=qtext, question_type="FREE_TEXT", weight=2.0, order_index=idx,
                )
                self.repo.db.add(fu)
                new_followups.append(fu)
                generated += 1
            ratings_out.append({
                "domain_code": cat["domain_code"], "domain_name": cat["domain_name"],
                "category_code": cat["category_code"], "category_name": cat["category_name"],
                "criteria_statement": cat["criteria_statement"],
                "score": r["score"], "verdict": r["verdict"],
                "rationale": r["rationale"], "missing": r["missing"],
                "followups": r["followups"],
                "items": r.get("items") or [],
            })
        await self.repo.db.flush()

        # Keep the questionnaire in sync: drop old AI follow-up ids, append new ones.
        questionnaire = await self.repo.get_questionnaire(assessment_id)
        if questionnaire:
            base = [qid for qid in (questionnaire.question_ids or []) if qid not in old_fu_ids]
            new_fu_ids = [f.id for f in new_followups]
            questionnaire.question_ids = base + new_fu_ids
            questionnaire.total_questions = len(questionnaire.question_ids)
            await self.repo.db.flush()

        if not ratings_out and errors:
            return {"ok": False, "error": f"Rating failed: {errors[-1]}"}

        return {
            "ok": True,
            "ratings": ratings_out,
            "generated": generated,
            "summary": {
                "categories_rated": len(ratings_out),
                "questions_generated": generated,
                "max_per_category": MAX_FOLLOWUPS,
                "evidence_files": ev_names,
            },
        }

    async def list_ai_results(self, assessment_id: str) -> dict:
        """Persisted category ratings + AI follow-up questions."""
        assessment = await self.repo.get_by_id(assessment_id)
        if not assessment:
            raise ValueError(f"Assessment {assessment_id} not found")
        ratings = sorted(
            assessment.category_ratings,
            key=lambda r: (r.domain_code or "", r.category_code or ""),
        )
        followups = sorted(
            [f for f in assessment.followups if getattr(f, "source", "ai") == "ai"],
            key=lambda f: (f.domain_code or "", f.category_code or "", f.order_index),
        )
        # Group follow-up question text under its category for per-rating display.
        fu_by_cat: dict[tuple, list] = {}
        for f in followups:
            fu_by_cat.setdefault((f.domain_code, f.category_code), []).append(f.text)
        return {
            "ratings": [
                {"domain_code": r.domain_code, "domain_name": r.domain_name,
                 "category_code": r.category_code, "category_name": r.category_name,
                 "criteria_statement": r.criteria_statement, "score": r.score,
                 "verdict": r.verdict, "rationale": r.rationale, "missing": r.missing or [],
                 "followups": fu_by_cat.get((r.domain_code, r.category_code), []),
                 "items": r.item_ratings or [],
                 "evidence_files": r.evidence_files or []}
                for r in ratings
            ],
            "followups": [
                {"id": f.id, "domain_code": f.domain_code, "category_code": f.category_code,
                 "category_name": f.category_name, "control_code": f.control_code,
                 "text": f.text, "question_type": f.question_type, "order_index": f.order_index}
                for f in followups
            ],
        }

    async def build_report(self, assessment_id: str) -> dict:
        """Structured assessment report built from the scored results."""
        a = await self.repo.get_by_id(assessment_id)
        if not a:
            raise ValueError(f"Assessment {assessment_id} not found")
        if a.status not in ("in_review", "completed"):
            raise PermissionError("Report is not available until the assessment has been submitted.")

        domain_scores = [
            {"domain_code": s.domain_code, "percentage": s.percentage,
             "maturity_level": s.maturity_level}
            for s in a.scores if s.level == "domain"
        ]
        domain_scores.sort(key=lambda d: d["percentage"])
        findings = [
            {"control_code": f.control_code, "domain_code": f.domain_code,
             "severity": f.severity, "title": f.title,
             "gap_description": f.gap_description, "recommendation": f.recommendation,
             "status": f.status}
            for f in a.findings
        ]
        by_sev: dict[str, int] = {}
        for f in a.findings:
            by_sev[f.severity] = by_sev.get(f.severity, 0) + 1

        return {
            "assessment": {
                "id": a.id, "name": a.name, "organization": a.organization,
                "status": a.status, "overall_score": a.overall_score,
                "maturity_level": a.maturity_level,
                "framework_ids": a.framework_ids,
            },
            "summary": (
                f"{a.name}: overall score {a.overall_score or 0}% "
                f"(maturity level {a.maturity_level or 0}/5) with {len(findings)} open finding(s)."
            ),
            "domain_scores": domain_scores,
            "findings_by_severity": by_sev,
            "findings": findings,
            "strengths": [d for d in domain_scores if d["percentage"] >= 80],
            "gaps": [d for d in domain_scores if d["percentage"] < 50],
        }

    async def _build_question_map(self, assessment) -> dict:
        """Map question_id -> question/control/domain metadata across the
        assessment's frameworks. Shared by report rendering and scoring.

        Scoped to the assessment's selected domains: when a domain subset is
        chosen, only questions belonging to those domains are included, so the
        scorer evaluates the questions with respect to the selected domains
        only. With no selection, the whole framework is in scope."""
        question_map: dict = {}
        selected = set(assessment.selected_domains or [])
        in_scope_domains: set[str] = set()
        for fw_id in assessment.framework_ids:
            fw = await self.fw_repo.get_by_id(fw_id)
            if not fw:
                continue
            for d in fw.domains:
                if selected and not _domain_selected(fw_id, d, selected):
                    continue
                in_scope_domains.add(d.code)
                for c in d.controls:
                    guide = c.maturity_criteria if isinstance(c.maturity_criteria, dict) else {}
                    item_type = guide.get("item_type") or guide.get("type")
                    for q in c.questions:
                        question_map[q.id] = {
                            "text": q.text,
                            "question_type": q.question_type,
                            "weight": q.weight,
                            "control_code": c.code,
                            "control_name": c.name,
                            "control_statement": c.statement,
                            "item_type": item_type,
                            "scope_cadence": guide.get("scope_cadence"),
                            "preferred_tooling": guide.get("preferred_tooling"),
                            "maturity_guide": guide,
                            "category_name": c.category_name,
                            "domain_code": d.code,
                            "domain_name": d.name,
                            "framework_name": fw.name,
                            "sub_topic": getattr(q, "sub_topic", None),
                            "maturity_signals": getattr(q, "maturity_signals", None),
                            "gap_if_deficient": getattr(q, "gap_if_deficient", None),
                        }
        # Per-assessment follow-up questions are scored too. AI follow-ups and
        # diagnostic questions are limited to in-scope domains; pre-assessment
        # questions are always in scope.
        for f in getattr(assessment, "followups", []) or []:
            src = getattr(f, "source", "ai")
            if src in ("ai", "diagnostic") and selected and f.domain_code not in in_scope_domains:
                continue
            question_map[f.id] = {
                "text": f.text,
                "question_type": f.question_type,
                "weight": f.weight,
                "control_code": f.control_code,
                "domain_code": f.domain_code,
                "domain_name": f.domain_name,
                "framework_name": None,
                "sub_topic": getattr(f, "sub_topic", None) or f.category_name,
                "maturity_signals": getattr(f, "maturity_signals", None),
                "gap_if_deficient": getattr(f, "gap_if_deficient", None),
            }
        return question_map

    async def build_report_dashboard(self, assessment_id: str) -> dict:
        """Build the cyber_prac report_data for the React Reports view, and
        persist the SAME numbers to the assessment so its stored maturity/score
        always matches the report (self-healing for stale/old-engine values)."""
        from backend.api.services.report_builder import build_report_with_scoring
        assessment = await self.repo.get_by_id(assessment_id)
        if not assessment:
            raise ValueError(f"Assessment {assessment_id} not found")
        if assessment.status not in ("in_review", "completed"):
            raise PermissionError("Report is not available until the assessment has been submitted.")
        question_map = await self._build_question_map(assessment)
        import asyncio
        # Run in thread pool — compute_scoring (inside) may invoke the LLM (blocking I/O).
        scoring, report = await asyncio.to_thread(build_report_with_scoring, assessment, question_map)
        # Refresh stored score/maturity/findings to match what the report shows.
        try:
            await self._persist_scoring(assessment_id, scoring, complete=False)
        except Exception as exc:  # never block the report on a persistence hiccup
            from backend.core.utils import logger
            logger.warning("Report-view score refresh failed for %s: %s", assessment_id, exc)

        # Separate AI-generated follow-up questions that are still open from
        # regular scored questions. Unanswered follow-ups are open items, not
        # evidence of critical findings — merging them into the critical-finding
        # count over-states severity.
        followup_ids = {f.id for f in (getattr(assessment, "followups", None) or [])}
        if followup_ids and isinstance(report, dict):
            reviews = report.get("enriched_reviews") or []
            open_followups = [
                {
                    "question_id": r.get("question_id"),
                    "control_id": r.get("control_id") or "",
                    "domain_id": r.get("domain_id") or "",
                    "question_text": str(r.get("question_text") or "")[:120],
                }
                for r in reviews
                if r.get("question_id") in followup_ids
                and not str(r.get("response") or "").strip()
            ]
            report["open_followups"] = open_followups
            report["stats"] = report.get("stats") or {}
            report["stats"]["open_followup_count"] = len(open_followups)
            # Remove follow-up questions from enriched_reviews so they don't
            # inflate the critical/weak verdict counts shown in the stats widgets.
            report["enriched_reviews"] = [
                r for r in reviews if r.get("question_id") not in followup_ids
            ]
            report["stats"]["total"] = len(report["enriched_reviews"])

        # Attach an AI evidence-rating SUMMARY (full detail lives in the AI tab).
        ratings = list(assessment.category_ratings)
        if ratings and isinstance(report, dict):
            # For market-assessment: compute the distribution of AI-determined 0-3
            # maturity levels from item_ratings so the summary aligns with the
            # market analytics sections above rather than showing disconnected 0-100 scores.
            ai_level_dist: dict[int, int] = {0: 0, 1: 0, 2: 0, 3: 0}
            ai_ctrl_count = 0
            for cr in ratings:
                for it in (getattr(cr, "item_ratings", None) or []):
                    if isinstance(it, dict):
                        lvl = it.get("level")
                        if isinstance(lvl, int) and 0 <= lvl <= 3:
                            ai_level_dist[lvl] += 1
                            ai_ctrl_count += 1
            report["ai_rating_summary"] = {
                "categories_rated": len(ratings),
                "average_score": round(sum(r.score for r in ratings) / len(ratings), 1),
                "by_verdict": {
                    v: sum(1 for r in ratings if (r.verdict or "") == v)
                    for v in ("strong", "partial", "weak")
                },
                "weakest": [
                    {"domain_code": r.domain_code, "category_name": r.category_name,
                     "score": r.score, "verdict": r.verdict}
                    for r in sorted(ratings, key=lambda r: r.score)[:5]
                ],
                # 0-3 level breakdown of AI-rated controls (market-assessment only).
                # These directly feed the market analytics levels shown above.
                "ai_level_distribution": ai_level_dist if ai_ctrl_count > 0 else None,
                "ai_controls_rated": ai_ctrl_count if ai_ctrl_count > 0 else None,
            }
        return report


    async def evidence_requirements(self, assessment_id: str) -> dict:
        """List the evidence required for the assessment's in-scope controls,
        plus what's already been uploaded (so the user knows what to provide
        before pre-filling)."""
        assessment = await self.repo.get_by_id(assessment_id)
        if not assessment:
            raise ValueError(f"Assessment {assessment_id} not found")

        selected = set(assessment.selected_domains or [])
        requirements: list[dict] = []
        all_types: set[str] = set()
        for fw_id in assessment.framework_ids:
            fw = await self.fw_repo.get_by_id(fw_id)
            if not fw:
                continue
            for d in fw.domains:
                if selected and not _domain_selected(fw_id, d, selected):
                    continue
                for c in d.controls:
                    ev = []
                    for q in (c.questions or []):
                        ev.extend(q.expected_evidence_types or [])
                    ev = sorted(set(ev))
                    all_types.update(ev)
                    requirements.append({
                        "control_code": c.code,
                        "domain_code": d.code,
                        "domain_name": d.name,
                        "statement": c.statement,
                        "expected_evidence_types": ev,
                    })

        ev_dir = os.path.join(settings.EVIDENCE_FOLDER, assessment_id)
        uploaded = _list_raw_evidence(ev_dir)

        return {
            "count": len(requirements),
            "evidence_types": sorted(all_types),
            "requirements": requirements,
            "uploaded": uploaded,
        }

    # ── Document requests (assessor → owner → assessor) ─────────────────────

    async def _framework(self, framework_id: str):
        """Framework with its domains/controls/questions, cached per request."""
        if framework_id not in self._fw_cache:
            self._fw_cache[framework_id] = await self.fw_repo.get_by_id(framework_id)
        return self._fw_cache[framework_id]

    async def _guide_index(self, assessment: Assessment) -> dict:
        """Index of the assessment's in-scope controls, used to explain to an
        owner WHAT a document request is actually asking for.

        The control statement is the guide: it is the framework's own wording of
        what has to be true, so it tells the owner what artefact will satisfy it.
        The category criteria statement and the questions' expected evidence
        types round that out.
        """
        if assessment.id in self._guide_cache:
            return self._guide_cache[assessment.id]

        selected = set(assessment.selected_domains or [])
        by_control: dict[str, dict] = {}
        by_domain: dict[str, dict] = {}
        by_evidence: dict[str, list[str]] = {}
        frameworks: list[str] = []

        for fw_id in (assessment.framework_ids or []):
            fw = await self._framework(fw_id)
            if not fw:
                continue
            frameworks.append(fw.name or fw.code)
            for d in fw.domains:
                if selected and not _domain_selected(fw_id, d, selected):
                    continue
                dom = by_domain.setdefault(
                    d.code, {"code": d.code, "name": d.name,
                             "description": d.description, "controls": []},
                )
                # Criteria statements live on the Category (sub-domain). Read
                # them off the eagerly-loaded d.categories rather than
                # c.category, which would lazy-load under async and blow up.
                criteria = {
                    (cat.code or ""): (cat.criteria_statement, cat.name)
                    for cat in (d.categories or [])
                }
                for c in d.controls:
                    cat_criteria, cat_name = criteria.get(c.category_code or "", (None, None))
                    ev: list[str] = []
                    for q in (c.questions or []):
                        ev.extend(q.expected_evidence_types or [])
                    ev = sorted(set(e for e in ev if e))
                    code = (c.code or "").strip()
                    entry = {
                        "framework": fw.name or fw.code,
                        "control_code": code,
                        "control_name": c.name,
                        "control_statement": c.statement,
                        "criteria_statement": cat_criteria,
                        "category_name": c.category_name or cat_name,
                        "domain_code": d.code,
                        "domain_name": d.name,
                        "expected_evidence_types": ev,
                    }
                    # Pre-tokenised search surfaces for the keyword fallback.
                    entry["_name_tokens"] = _tokens(f"{code} {c.name or ''}")
                    entry["_body_tokens"] = _tokens(
                        " ".join([c.statement or "", d.name or "", *ev])
                    )
                    by_control[code.upper()] = entry
                    dom["controls"].append(code)
                    for e in ev:
                        by_evidence.setdefault(e.strip().lower(), []).append(code)

        index = {
            "by_control": by_control, "by_domain": by_domain,
            "by_evidence": by_evidence, "frameworks": frameworks,
        }
        self._guide_cache[assessment.id] = index
        return index

    @staticmethod
    def _match_controls(query: str, index: dict, domain_code: str | None) -> list[dict]:
        """Best-effort control matches for a request that carries no control_code.

        Ranks the in-scope controls by how well their code/name/statement match
        the words of the request (its note + evidence type). A control only
        qualifies if its NAME contains a *distinctive* word from the request —
        one that names at most `_DISTINCTIVE_MAX` controls. That is what stops
        broad words like "report" or "security" from "matching" half the
        framework: when nothing distinctive lines up we return [] and the owner
        just gets the generic evidence-type hint, which is honest.
        """
        by_control = index["by_control"]
        if not by_control:
            return []
        candidates = list(by_control.values())
        if domain_code:
            scoped = [c for c in candidates if c["domain_code"] == domain_code]
            candidates = scoped or candidates

        useful, distinctive = set(), set()
        for tok in _tokens(query):
            hits = sum(1 for c in by_control.values() if tok in c["_name_tokens"])
            if hits:
                useful.add(tok)
                if hits <= _DISTINCTIVE_MAX:
                    distinctive.add(tok)
        if not distinctive:
            return []

        scored = []
        for c in candidates:
            if not (distinctive & c["_name_tokens"]):
                continue
            name_hits = len(useful & c["_name_tokens"])
            body_hits = len(useful & c["_body_tokens"])
            scored.append((name_hits * 3 + body_hits, c))
        scored.sort(key=lambda s: (-s[0], s[1]["control_code"]))
        return [c for _, c in scored[:3]]

    def _guidance_for(self, r: DocumentRequest, index: dict) -> dict:
        """Owner-facing guidance for one document request: the control statement
        that is being evidenced, plus what "good" looks like for this evidence
        type. Never contains scores, findings or other markets' data."""
        ev_type = (r.evidence_type or "").strip()
        by_control = index["by_control"]
        code = (r.control_code or "").strip().upper()
        ctrl = by_control.get(code)
        related: list[dict] = []
        source = "control" if ctrl else None

        if not ctrl:
            # Requests captured before control_code existed (or with a generic
            # label such as "policy") — rank the in-scope controls against the
            # words of the request instead. The assessor's note is the most
            # specific signal ("Latest vulnerability scan report"), so it leads.
            matches = self._match_controls(
                f"{r.note or ''} {ev_type}", index, r.domain_code,
            )
            related = [
                {k: m[k] for k in ("control_code", "control_name",
                                   "control_statement", "domain_name",
                                   "expected_evidence_types")}
                for m in matches
            ]
            if related:
                source = "evidence_type"

        dom = index["by_domain"].get(r.domain_code or "") or {}
        return {
            # How this guidance was resolved: "control" = exact control the
            # assessor tagged, "evidence_type" = related in-scope controls,
            # None = no framework match, only the generic hint applies.
            "source": source,
            "framework": (ctrl or {}).get("framework") or (index["frameworks"][0] if index["frameworks"] else None),
            "control_code": (ctrl or {}).get("control_code") or (r.control_code or None),
            "control_name": (ctrl or {}).get("control_name"),
            # The "guide": the framework's own statement of what must be true.
            "control_statement": (ctrl or {}).get("control_statement"),
            "criteria_statement": (ctrl or {}).get("criteria_statement"),
            "category_name": (ctrl or {}).get("category_name"),
            "domain_name": (ctrl or {}).get("domain_name") or r.domain_name or dom.get("name"),
            "domain_description": dom.get("description"),
            "expected_evidence_types": (ctrl or {}).get("expected_evidence_types") or [],
            "related_controls": related,
            "how_to_respond": EVIDENCE_TYPE_HINTS.get(ev_type.lower()),
        }

    def _request_dict(
        self, r: DocumentRequest, manifest: dict | None = None,
        guidance: dict | None = None,
    ) -> dict:
        files = []
        for sn in (r.provided_files or []):
            original = sn
            for d in (manifest or {}).get("documents", []):
                if d.get("stored_name") == sn:
                    original = d.get("original_name") or sn
                    break
            files.append({"stored_name": sn, "original_name": original})
        return {
            "id": r.id, "assessment_id": r.assessment_id,
            "evidence_type": r.evidence_type,
            "domain_code": r.domain_code, "domain_name": r.domain_name,
            "control_code": r.control_code,
            "note": r.note, "status": r.status,
            "requested_by": r.requested_by,
            "requested_at": r.requested_at.isoformat() if r.requested_at else None,
            "provided_files": files,
            "provided_by": r.provided_by,
            "provided_at": r.provided_at.isoformat() if r.provided_at else None,
            "review_note": r.review_note,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            # Owner-facing "how do I answer this" block (control statement +
            # expected evidence). Absent on the lightweight create/provide
            # responses, present on the list endpoints the owner reads.
            "guidance": guidance,
        }

    async def _get_request(self, assessment_id: str, request_id: str) -> DocumentRequest:
        result = await self.repo.db.execute(
            select(DocumentRequest).where(
                DocumentRequest.id == request_id,
                DocumentRequest.assessment_id == assessment_id,
            )
        )
        req = result.scalar_one_or_none()
        if not req:
            raise ValueError(f"Document request {request_id} not found")
        return req

    async def list_document_requests(self, assessment_id: str) -> list[dict]:
        assessment = await self.repo.get_by_id(assessment_id)
        if not assessment:
            raise ValueError(f"Assessment {assessment_id} not found")
        stmt = (
            select(DocumentRequest)
            .where(DocumentRequest.assessment_id == assessment_id)
            .order_by(DocumentRequest.requested_at.desc())
        )
        result = await self.repo.db.execute(stmt)
        manifest = self._load_manifest(assessment_id)
        index = await self._guide_index(assessment)
        out = []
        for (r,) in result.all():
            d = self._request_dict(r, manifest, self._guidance_for(r, index))
            out.append(d)
        return out

    async def list_document_requests_all(
        self, status: str | None = None, caller=None,
    ) -> list[dict]:
        """Cross-assessment request list — the owner's inbox feed.

        When `caller` is an owner the feed is narrowed to the assessments they
        are involved in (see `owner_assessment_ids`), so one market's owner can
        never read another market's requests. Any other role — and an
        unidentified caller — gets the full cross-assessment feed as before.
        """
        stmt = (
            select(DocumentRequest, Assessment.name, Assessment.organization,
                   Assessment.market_label)
            .join(Assessment, DocumentRequest.assessment_id == Assessment.id)
            .order_by(DocumentRequest.requested_at.desc())
        )
        if status:
            stmt = stmt.where(DocumentRequest.status == status)
        allowed: set[str] | None = None
        if getattr(caller, "role", None) == "owner":
            allowed = await self.owner_assessment_ids(caller)
        result = await self.repo.db.execute(stmt)
        manifests: dict[str, dict] = {}
        indexes: dict[str, dict] = {}
        out = []
        for r, name, org, market_label in result.all():
            if allowed is not None and r.assessment_id not in allowed:
                continue
            if r.assessment_id not in manifests:
                manifests[r.assessment_id] = self._load_manifest(r.assessment_id)
            if r.assessment_id not in indexes:
                assessment = await self.repo.get_by_id(r.assessment_id)
                indexes[r.assessment_id] = (
                    await self._guide_index(assessment) if assessment
                    else {"by_control": {}, "by_domain": {}, "by_evidence": {}, "frameworks": []}
                )
            d = self._request_dict(
                r, manifests[r.assessment_id],
                self._guidance_for(r, indexes[r.assessment_id]),
            )
            d["assessment_name"] = name
            d["organization"] = org
            d["market_label"] = market_label
            out.append(d)
        return out

    async def create_document_requests(
        self, assessment_id: str, items: list[dict],
        requested_by: str | None = None, note: str | None = None,
    ) -> list[dict]:
        """Assessor requests one row per evidence type; the owner sees them in
        the inbox and uploads against each."""
        assessment = await self.repo.get_by_id(assessment_id)
        if not assessment:
            raise ValueError(f"Assessment {assessment_id} not found")
        created = []
        for item in items:
            ev_type = str(item.get("evidence_type") or "").strip()
            if not ev_type:
                continue
            req = DocumentRequest(
                assessment_id=assessment_id,
                evidence_type=ev_type,
                domain_code=item.get("domain_code"),
                domain_name=item.get("domain_name"),
                control_code=item.get("control_code"),
                note=item.get("note") or note,
                requested_by=requested_by,
            )
            self.repo.db.add(req)
            created.append(req)
        await self.repo.db.flush()
        return [self._request_dict(r) for r in created]

    async def provide_documents(
        self, assessment_id: str, request_id: str, files: list[tuple[str, bytes]],
        provided_by: str | None = None,
    ) -> dict:
        """Owner fulfils a request: files are stored as engagement evidence (with
        text sidecars so pre-fill/rating can read them) and linked to the request."""
        req = await self._get_request(assessment_id, request_id)
        if not files:
            raise ValueError("No files provided")
        entries, _ = self._store_engagement_files(
            assessment_id, files, DEFAULT_DOC_TYPE
        )
        req.provided_files = list(req.provided_files or []) + [
            e["stored_name"] for e in entries
        ]
        req.status = "provided"
        req.provided_at = datetime.utcnow()
        if provided_by:
            req.provided_by = provided_by
        await self.repo.db.flush()
        return self._request_dict(req, self._load_manifest(assessment_id))

    async def review_document_request(
        self, assessment_id: str, request_id: str, action: str,
        note: str | None = None,
    ) -> dict:
        """Assessor accepts or rejects the files provided for a request."""
        if action not in ("accept", "reject"):
            raise ValueError("action must be 'accept' or 'reject'")
        req = await self._get_request(assessment_id, request_id)
        req.status = "accepted" if action == "accept" else "rejected"
        req.review_note = note
        req.reviewed_at = datetime.utcnow()
        await self.repo.db.flush()
        return self._request_dict(req, self._load_manifest(assessment_id))

    async def delete_document_request(self, assessment_id: str, request_id: str) -> bool:
        """Assessor withdraws a request. Any files already provided stay in the
        engagement documents, where the assessor manages them."""
        req = await self._get_request(assessment_id, request_id)
        await self.repo.db.delete(req)
        await self.repo.db.flush()
        return True

    # ── Prowler-generated evidence (owner self-service) ──────────────────────
    def _generated_dir(self, assessment_id: str) -> str:
        """Staging area for generated-but-not-yet-submitted Prowler reports.

        Kept OUTSIDE the assessment's evidence folder so a staged report never
        shows up in engagement documents until the owner submits it."""
        return os.path.join(settings.EVIDENCE_FOLDER, "_generated", assessment_id)

    async def generate_prowler_report(
        self, assessment_id: str, request_id: str,
    ) -> dict:
        """Owner triggers a (simulated) Prowler scan for a request. The report is
        generated once, staged for preview, and returned read-only. It is NOT yet
        linked to the request — the owner submits or discards it next."""
        import asyncio

        from backend.api.services import prowler_service

        req = await self._get_request(assessment_id, request_id)
        assessment = await self.repo.get_by_id(assessment_id)

        # Enrich the prompt with the framework's own control statement + expected
        # evidence for THIS request, so the LLM scopes the scan to exactly what is
        # being asked for rather than a generic report.
        control_statement, expected_evidence, criteria_statement = None, None, None
        if assessment:
            try:
                index = await self._guide_index(assessment)
                guidance = self._guidance_for(req, index)
                control_statement = guidance.get("control_statement")
                criteria_statement = guidance.get("criteria_statement")
                expected_evidence = guidance.get("expected_evidence_types") or None
            except Exception:  # noqa: BLE001 — guidance is best-effort enrichment
                pass

        # Cap staged reports per assessment to prevent disk exhaustion.
        stage_dir = self._generated_dir(assessment_id)
        os.makedirs(stage_dir, exist_ok=True)
        _STAGED_LIMIT = 20
        staged_files = [f for f in os.listdir(stage_dir) if not f.startswith(".")]
        if len(staged_files) >= _STAGED_LIMIT:
            raise ValueError(
                f"Too many staged Prowler reports ({_STAGED_LIMIT}) for this assessment. "
                "Submit or discard existing staged reports before generating new ones."
            )

        # generate_report performs a blocking LLM HTTP call — run it off the event
        # loop so the API stays responsive.
        filename, content = await asyncio.to_thread(
            prowler_service.generate_report,
            domain_code=req.domain_code,
            domain_name=req.domain_name,
            control_code=req.control_code,
            evidence_type=req.evidence_type,
            organization=getattr(assessment, "organization", None),
            control_statement=control_statement,
            criteria_statement=criteria_statement,
            expected_evidence=expected_evidence,
            assessor_note=req.note,
        )
        report_id = f"{uuid.uuid4().hex}_{filename}"
        with open(os.path.join(stage_dir, report_id), "w", encoding="utf-8") as fh:
            fh.write(content)
        return {
            "report_id": report_id,
            "filename": filename,
            "content": content,
            "source": "prowler",
            "immutable": True,
        }

    async def submit_prowler_report(
        self, assessment_id: str, request_id: str, report_id: str,
        provided_by: str | None = None,
    ) -> dict:
        """Owner submits a previously generated Prowler report. The staged file is
        promoted to engagement evidence (flagged immutable) and linked to the
        request, mirroring the upload/provide flow."""
        req = await self._get_request(assessment_id, request_id)
        stage_dir = os.path.abspath(self._generated_dir(assessment_id))
        safe_id = os.path.basename(report_id or "")
        staged = os.path.abspath(os.path.join(stage_dir, safe_id))
        if not safe_id or os.path.dirname(staged) != stage_dir or not os.path.isfile(staged):
            raise ValueError("Generated report not found — regenerate before submitting")

        with open(staged, encoding="utf-8") as fh:
            content = fh.read()
        # Original filename is the report_id minus the uuid prefix.
        original_name = safe_id.split("_", 1)[1] if "_" in safe_id else safe_id
        entries, _ = self._store_engagement_files(
            assessment_id, [(original_name, content.encode("utf-8"))], DEFAULT_DOC_TYPE,
            extra={"source": "prowler", "immutable": True},
        )
        req.provided_files = list(req.provided_files or []) + [
            e["stored_name"] for e in entries
        ]
        req.status = "provided"
        req.provided_at = datetime.utcnow()
        if provided_by:
            req.provided_by = provided_by
        await self.repo.db.flush()
        # Clean up the staging copy now that it's part of the engagement record.
        try:
            os.remove(staged)
        except OSError:
            pass
        return self._request_dict(req, self._load_manifest(assessment_id))

    # ── Engagement documents (bulk evidence) ────────────────────────────────
    def _manifest_path(self, assessment_id: str) -> str:
        return os.path.join(settings.EVIDENCE_FOLDER, assessment_id, MANIFEST_NAME)

    def _load_manifest(self, assessment_id: str) -> dict:
        path = self._manifest_path(assessment_id)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and isinstance(data.get("documents"), list):
                    return data
            except Exception:
                pass
        return {"documents": []}

    def _save_manifest(self, assessment_id: str, manifest: dict) -> None:
        try:
            with open(self._manifest_path(assessment_id), "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2)
        except Exception:
            pass

    def _doc_type_for(self, manifest: dict, stored_name: str) -> str:
        for d in manifest.get("documents", []):
            if d.get("stored_name") == stored_name:
                return d.get("doc_type") or DEFAULT_DOC_TYPE
        return DEFAULT_DOC_TYPE

    @staticmethod
    def _doc_visible_to_market(entry_market_id: str | None, market_id: str | None) -> bool:
        """Per-market visibility for an engagement document.

        - market_id is None (central admin/assessor): everything is visible.
        - market_id set (a market assessor/owner): only that market's own files
          plus assessment-wide/shared files (entry market_id is empty) — never
          another market's files.
        """
        if not market_id:
            return True
        return (entry_market_id or None) in (None, market_id)

    async def upload_evidence_bulk(
        self, assessment_id: str, files: list[tuple[str, bytes]],
        doc_type: str = DEFAULT_DOC_TYPE,
    ) -> dict:
        """Accept multiple engagement documents for the whole assessment (not tied
        to a single question), tagged with a doc_type bucket (survey | transcript
        | evidence | policy). Saved with text sidecars so pre-fill can use them
        and recorded in the engagement manifest."""
        assessment = await self.repo.get_by_id(assessment_id)
        if not assessment:
            raise ValueError(f"Assessment {assessment_id} not found")

        doc_type = doc_type if doc_type in DOC_TYPES else DEFAULT_DOC_TYPE
        readiness = "[compliance-readiness]" in (assessment.description or "")
        entries, extracted = self._store_engagement_files(
            assessment_id, files, doc_type, deduplicate=readiness,
        )
        return {"ok": True, "uploaded": [e["original_name"] for e in entries],
                "count": len(entries), "text_extracted": extracted, "doc_type": doc_type,
                "duplicate_documents_skipped": max(0, len(files) - len(entries))}

    def _store_engagement_files(
        self, assessment_id: str, files: list[tuple[str, bytes]], doc_type: str,
        extra: dict | None = None, deduplicate: bool = False,
    ) -> tuple[list[dict], int]:
        """Save files into the assessment's evidence folder with text sidecars and
        manifest entries. Returns (manifest entries created, sidecars extracted).

        `extra` merges additional keys into every manifest entry — used to flag
        Prowler-generated reports (source="prowler", immutable=True)."""
        ev_dir = os.path.join(settings.EVIDENCE_FOLDER, assessment_id)
        os.makedirs(ev_dir, exist_ok=True)

        from backend.api.services.prefill import extract_evidence_text

        manifest = self._load_manifest(assessment_id)
        known_hashes = {
            item.get("hash") for item in manifest.get("documents", []) if item.get("hash")
        }
        entries, extracted = [], 0
        for file_name, content in files:
            content_hash = hashlib.sha256(content).hexdigest() if deduplicate else None
            if content_hash and content_hash in known_hashes:
                continue
            safe_name = f"{uuid.uuid4()}_{_safe_basename(file_name)}"
            fp = os.path.join(ev_dir, safe_name)
            with open(fp, "wb") as f:
                f.write(content)
            preview = extract_evidence_text(fp) or ""
            if preview:
                try:
                    with open(fp + ".preview.txt", "w", encoding="utf-8") as fh:
                        fh.write(preview)
                    extracted += 1
                except Exception:
                    pass
            entry = {
                "stored_name": safe_name,
                "original_name": file_name,
                "doc_type": doc_type,
                "size": len(content),
                "uploaded_at": datetime.utcnow().isoformat(),
            }
            if content_hash:
                entry["hash"] = content_hash
                known_hashes.add(content_hash)
            if extra:
                entry.update(extra)
            manifest["documents"].append(entry)
            entries.append(entry)

        self._save_manifest(assessment_id, manifest)
        return entries, extracted

    async def list_engagement_documents(self, assessment_id: str) -> dict:
        """Engagement documents grouped by bucket, plus the expected evidence for
        each in-scope category (so the Evidence bucket can show what to provide)."""
        assessment = await self.repo.get_by_id(assessment_id)
        if not assessment:
            raise ValueError(f"Assessment {assessment_id} not found")

        ev_dir = os.path.join(settings.EVIDENCE_FOLDER, assessment_id)
        raw = set(_list_raw_evidence(ev_dir))
        manifest = self._load_manifest(assessment_id)

        by_type: dict[str, list] = {dt: [] for dt in DOC_TYPES}
        seen = set()
        for d in manifest.get("documents", []):
            sn = d.get("stored_name")
            if sn not in raw:
                continue
            seen.add(sn)
            dt = d.get("doc_type") if d.get("doc_type") in DOC_TYPES else DEFAULT_DOC_TYPE
            by_type[dt].append({
                "stored_name": sn,
                "original_name": d.get("original_name") or sn,
                "doc_type": dt,
                "size": d.get("size"),
                "uploaded_at": d.get("uploaded_at"),
            })
        # Files on disk with no manifest entry — treated as untagged evidence.
        for sn in sorted(raw - seen):
            by_type[DEFAULT_DOC_TYPE].append({
                "stored_name": sn, "original_name": sn,
                "doc_type": DEFAULT_DOC_TYPE,
                "size": None, "uploaded_at": None,
            })

        # Expected evidence per in-scope category (for the Evidence bucket hint).
        selected = set(assessment.selected_domains or [])
        expected_by_category: list[dict] = []
        for fw_id in assessment.framework_ids:
            fw = await self.fw_repo.get_by_id(fw_id)
            if not fw:
                continue
            for d in fw.domains:
                if selected and not _domain_selected(fw_id, d, selected):
                    continue
                for cat in d.categories:
                    ev: list[str] = []
                    for c in cat.controls:
                        for q in (c.questions or []):
                            ev.extend(q.expected_evidence_types or [])
                    expected_by_category.append({
                        "domain_code": d.code,
                        "domain_name": d.name,
                        "category_code": cat.code,
                        "category_name": cat.name,
                        "criteria_statement": cat.criteria_statement,
                        "expected_evidence_types": sorted(set(ev)),
                    })

        return {
            "doc_types": list(DOC_TYPES),
            "by_type": by_type,
            "counts": {dt: len(by_type[dt]) for dt in DOC_TYPES},
            "total": sum(len(v) for v in by_type.values()),
            "expected_by_category": expected_by_category,
        }

    async def delete_evidence_file(self, assessment_id: str, file_name: str) -> bool:
        """Delete a single bulk-evidence file (and its cached text sidecar) from
        this assessment's evidence folder. Returns False if the file is missing."""
        ev_dir = os.path.abspath(os.path.join(settings.EVIDENCE_FOLDER, assessment_id))
        # Guard against path traversal: only allow a bare filename inside ev_dir.
        safe_name = os.path.basename(file_name or "")
        if not safe_name:
            return False
        fp = os.path.abspath(os.path.join(ev_dir, safe_name))
        if os.path.dirname(fp) != ev_dir or not os.path.isfile(fp):
            return False
        try:
            os.remove(fp)
        except OSError:
            return False
        sidecar = fp + ".preview.txt"
        if os.path.isfile(sidecar):
            try:
                os.remove(sidecar)
            except OSError:
                pass
        # Prune the manifest entry for this file.
        manifest = self._load_manifest(assessment_id)
        before = len(manifest.get("documents", []))
        manifest["documents"] = [
            d for d in manifest.get("documents", []) if d.get("stored_name") != safe_name
        ]
        if len(manifest["documents"]) != before:
            self._save_manifest(assessment_id, manifest)
        return True


