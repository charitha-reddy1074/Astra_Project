import asyncio
import os
import json
import re
import uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.repositories.framework_repo import FrameworkRepository
from backend.api.models.framework import Framework, Domain, Category, Control, Question, FrameworkMapping
from backend.api.config import settings


def _normalise_canonical(data: dict) -> dict:
    """Normalise any accepted framework JSON shape into the internal API shape.

    Dispatched cheapest-first, and idempotent:

      1. drop-in framework dataset (``{"framework": {"domains": [
         {"domain_id", "sub_domains": [...]}]}}``) — see
         ``compliance/dataset.py``; this is the format of the bundled
         ``nist_csf_2_0.json`` and ``soc2_tsc_2017.json``;
      2. legacy root/cyber_prac pipeline shape (``framework_id`` +
         ``domains[].domain_id`` + ``control_id``);
      3. API canonical shape (``code`` + ``domains[].categories[]``) — returned
         unchanged.

    Both the DB importer and the Chroma chunker consume the result, so this is
    applied once per file rather than in each caller — otherwise a file gets one
    shape in the database and a different one in the vector store.
    """
    from backend.api.compliance.dataset import dataset_to_api_canonical, is_dataset

    if isinstance(data, dict) and is_dataset(data):
        fw = data.get("framework") or {}
        return dataset_to_api_canonical(
            data,
            name_hint=str(fw.get("framework_name") or fw.get("name") or ""),
        )
    if _is_root_canonical(data):
        return _root_canonical_to_api(
            data,
            data.get("name") or data.get("framework_name", ""),
            str(data.get("version", "1.0")),
        )
    return data


class FrameworkService:
    def __init__(self, db: AsyncSession):
        self.repo = FrameworkRepository(db)

    async def list_frameworks(self) -> list[Framework]:
        return await self.repo.get_all()

    async def get_framework(self, framework_id: str) -> Framework | None:
        return await self.repo.get_by_id(framework_id)

    async def process_upload(self, filename: str, file_content: bytes) -> dict:
        """Ingest an uploaded framework using the ROOT intelligent pipeline.

        Runs backend.ingestion.dynamic_pipeline.ingest_file (Groq LLM control
        extraction + hierarchical chunking + embeddings into the LOCAL Chroma
        store + canonical JSON), then imports the resulting canonical framework
        into the DB. Each control gets exactly one question (auto-generated from
        its statement during import). Works for ARBITRARY frameworks, not just
        NIST/ISO/CIS.
        """
        os.makedirs(settings.RAW_FOLDER, exist_ok=True)
        file_path = os.path.join(settings.RAW_FOLDER, filename)
        with open(file_path, "wb") as f:
            f.write(file_content)

        # The upload carries no metadata — derive a name from the filename.
        framework_name = _clean_framework_name(os.path.splitext(filename)[0])
        framework_version = "1.0"

        # PDFs → robust column-aware extractor (complete control capture + one
        # LLM question per control, written into the canonical file). Other
        # formats → the generic Groq dynamic pipeline.
        ext = os.path.splitext(filename)[1].lower()
        if ext == ".pdf":
            from backend.ingestion.robust_pdf_extractor import ingest_pdf_framework
            summary = await asyncio.to_thread(
                ingest_pdf_framework, file_path, framework_name, framework_version
            )
        else:
            from backend.ingestion.dynamic_pipeline import ingest_file
            summary = await asyncio.to_thread(
                ingest_file,
                file_path,
                framework_name=framework_name,
                framework_version=framework_version,
            )

        # Import the canonical JSON the root pipeline produced into the DB.
        with open(summary["canonical_json_path"], "r", encoding="utf-8") as fh:
            root_canonical = json.load(fh)
        canonical = _root_canonical_to_api(root_canonical, framework_name, framework_version)
        framework = await self._store_canonical_json(canonical, filename)

        return {
            "framework_id": framework.id,
            "framework_name": framework.name,
            "domains": framework.total_domains,
            "controls": framework.total_controls,
            "questions": framework.total_questions,
            "collection": summary.get("collection_name"),
            "chunks": summary.get("n_chunks"),
        }

    async def import_json(self, canonical_json: dict) -> dict:
        """Import a pre-parsed canonical JSON framework.

        Accepts any of the three shapes `_normalise_canonical` recognises: the
        drop-in framework dataset, the legacy root pipeline shape, or the API
        canonical shape.
        """
        canonical_json = _normalise_canonical(canonical_json)
        framework = await self._store_canonical_json(canonical_json, "json_import")
        return {
            "framework_id": framework.id,
            "framework_name": framework.name,
            "domains": framework.total_domains,
            "controls": framework.total_controls,
            "questions": framework.total_questions,
        }

    async def _store_canonical_json(self, data: dict, source_file: str) -> Framework:
        """Convert canonical JSON dict into DB models."""
        code = data.get("code") or data.get("name", "UNKNOWN").upper().replace(" ", "_")

        # Upsert: preserve existing ID so assessment framework_ids remain valid
        existing = await self.repo.get_by_code(code)
        fw_id = existing.id if existing else str(uuid.uuid4())
        if existing:
            # Delete children via raw SQL to avoid ORM cascade/lazy-load issues in async context,
            # then expunge the stale Python object so we can re-add with the same PK.
            db = self.repo.db
            await db.execute(text("DELETE FROM questions WHERE framework_id = :fid"), {"fid": fw_id})
            await db.execute(text("DELETE FROM controls WHERE framework_id = :fid"), {"fid": fw_id})
            await db.execute(text("DELETE FROM categories WHERE framework_id = :fid"), {"fid": fw_id})
            await db.execute(text("DELETE FROM domains WHERE framework_id = :fid"), {"fid": fw_id})
            await db.execute(text("DELETE FROM framework_mappings WHERE source_framework_id = :fid"), {"fid": fw_id})
            await db.execute(text("DELETE FROM frameworks WHERE id = :fid"), {"fid": fw_id})
            await db.flush()
            db.expunge(existing)
        fw = Framework(
            id=fw_id,
            code=code,
            name=data.get("name", code),
            version=str(data.get("version", "1.0")),
            description=data.get("description"),
            source_file=source_file,
            maturity_levels=data.get("maturity_levels"),
        )

        domains_data = data.get("domains", [])
        total_controls = 0
        total_questions = 0
        total_named_categories = 0

        for d_idx, domain_data in enumerate(domains_data):
            domain_id = str(uuid.uuid4())
            domain = Domain(
                id=domain_id,
                framework_id=fw_id,
                code=domain_data.get("code", f"D{d_idx}"),
                name=domain_data.get("name", ""),
                description=domain_data.get("description"),
                order_index=d_idx,
            )

            # framework → domain → category (sub-domain) → control → question.
            # Frameworks without an explicit category layer get one synthetic
            # category per domain so the nesting is uniform.
            categories = domain_data.get("categories", [])
            if not categories:
                categories = [{
                    "code": domain_data.get("code"),
                    "name": None,
                    "criteria_statement": None,
                    "controls": domain_data.get("controls", []),
                }]

            # Count named (real) categories — synthetic pass-throughs have name=None
            total_named_categories += sum(1 for cat in categories if cat.get("name"))

            ctrl_idx = 0
            for cat_idx, cat in enumerate(categories):
                cat_id = str(uuid.uuid4())
                category = Category(
                    id=cat_id,
                    domain_id=domain_id,
                    framework_id=fw_id,
                    code=cat.get("code") or f"{domain.code}-CAT{cat_idx}",
                    name=cat.get("name"),
                    criteria_statement=cat.get("criteria_statement"),
                    description=cat.get("description"),
                    order_index=cat_idx,
                )
                for ctrl_data in cat.get("controls", []):
                    ctrl_id = str(uuid.uuid4())
                    ctrl = Control(
                        id=ctrl_id,
                        domain_id=domain_id,
                        category_id=cat_id,
                        framework_id=fw_id,
                        code=ctrl_data.get("code", f"C{ctrl_idx}"),
                        name=ctrl_data.get("name"),
                        statement=ctrl_data.get("statement"),
                        category_code=category.code,
                        category_name=cat.get("name"),
                        weight=float(ctrl_data.get("weight", 1.0)),
                        criticality=ctrl_data.get("criticality", "medium"),
                        maturity_level=ctrl_data.get("maturity_level"),
                        cross_refs=ctrl_data.get("cross_refs", []),
                        maturity_criteria=ctrl_data.get("maturity_criteria"),
                        order_index=ctrl_idx,
                    )

                    ctrl_questions = ctrl_data.get("questions", [])
                    if not ctrl_questions:
                        # Auto-generate a YES_NO question from the control statement/name
                        stmt = ctrl_data.get("statement", "")
                        name = ctrl_data.get("name", "")
                        c_code = ctrl_data.get("code", "")
                        if stmt:
                            q_text = stmt if len(stmt) <= 220 else stmt[:217] + "..."
                        elif name:
                            q_text = f"Has '{name}' been implemented?"
                        else:
                            q_text = f"Has control {c_code} been implemented?"
                        ctrl_questions = [{
                            "text": q_text,
                            "help_text": stmt or None,
                            "question_type": "YES_NO",
                            "weight": ctrl_data.get("weight", 1.0),
                            "expected_evidence_types": ctrl_data.get("expected_evidence_types", []),
                        }]

                    for q_idx, q_data in enumerate(ctrl_questions):
                        q = Question(
                            id=str(uuid.uuid4()),
                            control_id=ctrl_id,
                            framework_id=fw_id,
                            text=q_data.get("text", ""),
                            help_text=q_data.get("help_text"),
                            # Canonical JSON authors both "yes_no" and "YES_NO";
                            # normalise so the DB never holds mixed casing (the UI
                            # dispatches on this value and would otherwise render a
                            # yes/no question as a free-text box).
                            question_type=str(
                                q_data.get("question_type") or "YES_NO"
                            ).strip().upper(),
                            choices=q_data.get("choices"),
                            weight=float(q_data.get("weight", 1.0)),
                            maturity_level=q_data.get("maturity_level"),
                            expected_evidence_types=q_data.get("expected_evidence_types", []),
                            order_index=q_idx,
                        )
                        ctrl.questions.append(q)
                        total_questions += 1

                    category.controls.append(ctrl)
                    ctrl_idx += 1
                    total_controls += 1

                domain.categories.append(category)

            fw.domains.append(domain)

        fw.total_domains = len(fw.domains)
        fw.total_categories = total_named_categories
        fw.total_controls = total_controls
        fw.total_questions = total_questions

        for mapping in data.get("mappings", []):
            m = FrameworkMapping(
                id=str(uuid.uuid4()),
                source_framework_id=fw_id,
                target_framework_code=mapping.get("target_framework", ""),
                target_control_code=mapping.get("target_control", ""),
                relationship_type=mapping.get("relationship", "RELATED"),
                confidence=mapping.get("confidence"),
                notes=mapping.get("notes"),
            )
            fw.mappings_from.append(m)

        await self.repo.create(fw)
        return fw

    async def load_existing_canonical_files(self) -> list[dict]:
        """Load canonical JSON files from disk into DB and Chroma."""
        results = []
        failures = []
        canonical_dir = settings.CANONICAL_FOLDER
        if not os.path.exists(canonical_dir):
            return results

        for fname in sorted(os.listdir(canonical_dir)):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(canonical_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # Normalise once: the DB importer *and* the chunker below must
                # see the same API shape, or a dataset lands in the database as
                # 106 controls and in Chroma as nothing.
                data = _normalise_canonical(data)

                result = await self.import_json(data)

                # Upload chunks to Chroma
                try:
                    from backend.api.ingestion.chunkers.chunk_router import route_chunk_builder
                    from backend.api.embeddings.chroma_loader import upload_chunks
                    chunks = await asyncio.to_thread(route_chunk_builder, data)
                    collection_name = _chroma_collection(data.get("code", ""))
                    count = await asyncio.to_thread(upload_chunks, chunks, collection_name)
                    result["chunks_uploaded"] = count
                    result["collection"] = collection_name
                except Exception as e:
                    print(f"Chroma upload failed for {fname}: {e}")
                    result["chunks_uploaded"] = 0

                await self.repo.db.commit()
                results.append(result)
                print(f"Loaded: {fname} -> DB + Chroma")
            except Exception as e:
                # Roll back so a single bad file cannot poison the shared session
                # and cascade-fail every remaining framework.
                try:
                    await self.repo.db.rollback()
                except Exception:
                    pass
                failures.append({"file": fname, "error": str(e)})
                print(f"Failed to load {fname}: {e}")

        if failures:
            print(f"load-existing: {len(results)} imported, {len(failures)} failed")
        self.last_load_failures = failures
        return results

    async def delete_framework(self, framework_id: str) -> bool:
        return await self.repo.delete(framework_id)


def _chroma_collection(framework_code: str) -> str:
    """Derive a Chroma collection name from a framework code.

    Delegates to the shared implementation so ingestion and retrieval can never
    disagree on where a framework's vectors live.
    """
    from backend.api.embeddings.chroma_loader import collection_name_for_framework
    return collection_name_for_framework(framework_code)


def _clean_framework_name(stem: str) -> str:
    """Turn an uploaded filename stem into a human-readable framework name."""
    name = re.sub(r"\(.*?\)", "", stem)          # drop "(1)" style suffixes
    name = re.sub(r"[_\-]+", " ", name)           # underscores/dashes → spaces
    name = re.sub(r"\s+", " ", name).strip()
    return name or "Uploaded Framework"


def _root_control_to_api(c: dict) -> dict:
    """Map one root-shape control (control_id/control_statement/questions) into
    the API control shape consumed by _store_canonical_json."""
    sub_topic = c.get("sub_topic")
    # Enrich the maturity guide with the item's evaluation context (item type,
    # scope/cadence coverage expectation, preferred tooling, expected evidence)
    # so the AI rubric-scorer and the report can judge coverage/tooling without
    # a Control schema change. The Control model only persists maturity_criteria
    # as JSON, so these ride inside it (populated on the next framework import).
    guide = dict(c.get("maturity_guide") or {})
    if guide or c.get("item_type") or c.get("scope_cadence") or c.get("preferred_tooling"):
        guide.setdefault("type", c.get("item_type"))
        guide["item_type"] = c.get("item_type")
        guide["scope_cadence"] = c.get("scope_cadence")
        guide["preferred_tooling"] = c.get("preferred_tooling")
        guide["standard_referenced"] = c.get("standard_referenced")
        guide["expected_evidence_types"] = c.get("expected_evidence_types", [])
    return {
        "code": c.get("control_id"),
        # Use the sub_topic (where present, e.g. Market Assessment) as the
        # control name so it stays meaningful within its category.
        "name": sub_topic,
        "statement": c.get("control_statement"),
        "category_code": sub_topic,
        "category_name": sub_topic,
        "cross_refs": c.get("cross_references", []),
        "expected_evidence_types": c.get("expected_evidence_types", []),
        "maturity_level": None,
        # Carry verbatim maturity guides (0-3 Technology/Process rubric) plus the
        # item's scope/tooling/evidence context so the AI scorer and report have
        # per-control evaluation conditions.
        "maturity_criteria": guide or None,
        # Carry the canonical's questions into the DB. If absent,
        # _store_canonical_json auto-generates one per control.
        "questions": [
            {
                "text": q.get("question_text") or q.get("text", ""),
                "help_text": c.get("control_statement"),
                "question_type": q.get("question_type", "YES_NO"),
                "weight": q.get("weight", 3),
                "choices": q.get("choices"),
                "expected_evidence_types": q.get("expected_evidence_types", []),
            }
            for q in c.get("questions", [])
        ],
    }


def _root_canonical_to_api(root: dict, name: str, version: str) -> dict:
    """Map the root dynamic_pipeline canonical shape (domain_id/domain_name,
    control_id/control_statement) into the API's canonical shape consumed by
    _store_canonical_json. Controls carry no questions, so the importer
    auto-generates exactly one question per control."""
    code = (name or root.get("framework_name") or root.get("name", "FRAMEWORK")).upper().replace(" ", "_")
    domains = []
    for d in root.get("domains", []):
        raw_categories = d.get("categories")
        if raw_categories:
            # Already has a category (sub-domain) layer — map it through,
            # preserving each category's criteria_statement.
            categories = [
                {
                    "code": cat.get("category_id") or cat.get("code"),
                    "name": cat.get("category_name") or cat.get("name"),
                    "criteria_statement": cat.get("criteria_statement"),
                    "description": cat.get("description"),
                    "controls": [_root_control_to_api(c) for c in cat.get("controls", [])],
                }
                for cat in raw_categories
            ]
            domains.append({
                "code": d.get("domain_id"),
                "name": d.get("domain_name"),
                "description": d.get("description"),
                "categories": categories,
            })
        else:
            # No explicit categories — controls sit directly under the domain;
            # _store_canonical_json wraps them in one synthetic category.
            domains.append({
                "code": d.get("domain_id"),
                "name": d.get("domain_name"),
                "description": d.get("description"),
                "controls": [_root_control_to_api(c) for c in d.get("controls", [])],
            })
    return {
        "code": code,
        "name": root.get("framework_name") or root.get("name", name),
        "version": str(root.get("version", version)),
        "description": root.get("description"),
        "domains": domains,
    }


def _is_root_canonical(data: dict) -> bool:
    """True if `data` is the root/cyber_prac canonical shape (framework_id /
    domains[].domain_id / control_id) rather than the API shape (code /
    domains[].categories[].controls[].statement)."""
    if not isinstance(data, dict) or "code" in data:
        return False
    domains = data.get("domains") or []
    if domains and isinstance(domains[0], dict):
        d0 = domains[0]
        if "domain_id" in d0:
            return True
        ctrls = d0.get("controls") or []
        if ctrls and isinstance(ctrls[0], dict) and "control_id" in ctrls[0]:
            return True
    return "framework_id" in data and "code" not in data
