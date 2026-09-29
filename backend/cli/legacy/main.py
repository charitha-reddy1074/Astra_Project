"""
main.py — Compliance RAG Pipeline Orchestrator.

Ingestion is handled separately:
    python ingest_frameworks.py --file doc.pdf --name "PCI DSS" --version "4.0"

This script runs the downstream pipeline steps:
 1  Generate Market Assessment questionnaires (per framework, per domain)
 2  Cross-framework mapping demo (NIST CSF 2.0 → CSF 1.1)
 3  RAG demo: free-form Q&A
 4  RAG demo: topic-based questionnaire generation
 5  Parse & evaluate evidence file
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# ── Bootstrap import path + load environment ───────────────────────────────
BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
import backend.config.settings  # noqa: F401  (loads backend/.env once)

LLM_API_KEY = os.getenv("GROQ_API_KEY", "")
if not LLM_API_KEY:
    print("[ERROR] GROQ_API_KEY not set in .env")
    sys.exit(1)

# ── Project paths ─────────────────────────────────────────────────────────
DATA_DIR       = BASE_DIR / "data"
FRAMEWORKS_DIR = DATA_DIR / "frameworks"
EVIDENCES_DIR  = DATA_DIR / "evidences"
OUTPUTS_DIR    = DATA_DIR / "outputs"
CHROMA_DIR     = Path(os.getenv("CHROMA_PATH", str(BASE_DIR / "chroma_db")))

for d in [FRAMEWORKS_DIR, EVIDENCES_DIR, OUTPUTS_DIR, CHROMA_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── App imports ───────────────────────────────────────────────────────────
from backend.core.utils import (
    print_banner, print_section, print_kv, print_dict,
    print_verdict, save_json, load_json, logger, timestamp,
)
from backend.services.normalizer import FrameworkNormalizer
from backend.services.chunker import HierarchicalChunker
from backend.data_access.vectordb import VectorDBManager
from backend.services.questionnaire import QuestionnaireBuilder
from backend.cli.legacy.evaluator import EvidenceEvaluator
from backend.services.rag_pipeline import RAGPipeline
from backend.cli.legacy.cross_mapper import CrossFrameworkMapper
from backend.services.framework_exporter import (
    save_framework_exports,
    load_framework_catalog,
    save_questionnaire,
    canonicalize_framework,
    NIST_EXPORT_PATH,
    ISO_EXPORT_PATH,
    CIS_EXPORT_PATH,
)


# ═════════════════════════════════════════════════════════════════════════════
# Sample evidence bootstrap
# ═════════════════════════════════════════════════════════════════════════════

def bootstrap_sample_evidence() -> Path:
    """Write a realistic sample evidence file to data/evidences/ if absent."""
    path = EVIDENCES_DIR / "acme_corp_evidence.json"
    if not path.exists():
        evidence = {
            "organisation":   "Acme Corp",
            "assessment_date": "2025-01-15",
            "evidence_items": [
                {
                    "category":    "Identity & Access Management",
                    "description": (
                        "Acme Corp maintains a centralised IAM platform using Okta. "
                        "All user accounts require MFA. Quarterly access reviews are "
                        "conducted and documented in Jira. Privileged accounts use "
                        "hardware tokens. A formal access control policy is in place "
                        "and acknowledged by all staff annually."
                    ),
                    "artefacts":   ["access_control_policy_v3.pdf", "okta_mfa_config.png",
                                    "q3_2024_access_review.xlsx"],
                },
                {
                    "category":    "Vulnerability Management",
                    "description": (
                        "Qualys is used for weekly vulnerability scans across all "
                        "production and staging environments. Critical and high findings "
                        "are patched within 30 days per SLA. The vulnerability register "
                        "is reviewed monthly by the CISO. A formal patch management "
                        "procedure exists."
                    ),
                    "artefacts":   ["qualys_scan_dec2024.pdf", "patch_management_policy.pdf",
                                    "vuln_register_jan2025.xlsx"],
                },
                {
                    "category":    "Security Monitoring",
                    "description": (
                        "A Splunk SIEM is deployed and ingesting logs from network devices, "
                        "servers, cloud workloads, and endpoints. A 24/7 SOC monitors alerts. "
                        "IDS/IPS rules are reviewed quarterly. Threat intelligence feeds from "
                        "FS-ISAC are integrated."
                    ),
                    "artefacts":   ["splunk_architecture.pdf", "soc_runbook.pdf",
                                    "ids_rule_review_q4_2024.docx"],
                },
                {
                    "category":    "Incident Response",
                    "description": (
                        "Acme Corp has a documented Incident Response Plan (IRP) reviewed "
                        "annually. A tabletop exercise was conducted in November 2024. "
                        "Incidents are tracked in ServiceNow. Post-incident reviews are "
                        "completed within 5 days of resolution."
                    ),
                    "artefacts":   ["incident_response_plan_2024.pdf",
                                    "tabletop_exercise_report_nov2024.pdf"],
                },
                {
                    "category":    "Data Protection",
                    "description": (
                        "All data at rest is encrypted using AES-256. TLS 1.2+ is enforced "
                        "for all data in transit. A data classification policy exists with "
                        "four tiers: Public, Internal, Confidential, and Restricted. "
                        "DLP tools monitor for sensitive data exfiltration."
                    ),
                    "artefacts":   ["data_classification_policy.pdf", "encryption_standard.pdf",
                                    "dlp_configuration.pdf"],
                },
                {
                    "category":    "Risk Management",
                    "description": (
                        "An enterprise risk register is maintained and reviewed quarterly "
                        "by the Risk Committee. A formal risk appetite statement has been "
                        "approved by the Board. Third-party vendors undergo annual risk "
                        "assessments prior to contract renewal."
                    ),
                    "artefacts":   ["risk_register_q4_2024.xlsx", "risk_appetite_statement.pdf",
                                    "vendor_risk_assessment_template.xlsx"],
                },
                {
                    "category":    "Security Awareness Training",
                    "description": (
                        "All employees complete mandatory security awareness training upon "
                        "onboarding and annually thereafter via KnowBe4. Phishing simulation "
                        "campaigns are run quarterly. Completion rates exceed 95%."
                    ),
                    "artefacts":   ["training_completion_report_2024.pdf",
                                    "phishing_simulation_q3_2024.xlsx"],
                },
                {
                    "category":    "Backup and Recovery",
                    "description": (
                        "Daily backups are taken of all critical systems with 30-day retention. "
                        "Backups are tested monthly. Recovery procedures are documented. "
                        "RTO is 4 hours and RPO is 1 hour for Tier-1 systems. "
                        "A DR test was successfully completed in October 2024."
                    ),
                    "artefacts":   ["backup_policy.pdf", "dr_test_report_oct2024.pdf",
                                    "rto_rpo_matrix.xlsx"],
                },
            ],
            "overall_maturity_self_assessment": "Defined",
            "auditor_notes": (
                "Evidence collected by internal audit team. "
                "External pen test scheduled for Q2 2025."
            ),
        }
        save_json(evidence, path)
        logger.info("Created sample evidence file: %s", path)
    return path


# ═════════════════════════════════════════════════════════════════════════════
# Main pipeline
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    print_banner("Cyber Security Frameworks RAG Compliance Pipeline  (LangChain + Groq)")

    # ── Setup ────────────────────────────────────────────────────────────
    vectordb = VectorDBManager()
    chunker  = HierarchicalChunker()
    ev_path  = bootstrap_sample_evidence()

    # Load NIST canonical framework for demo steps (cross-mapping + evidence eval)
    nist_fw: dict = {}
    if NIST_EXPORT_PATH.exists():
        nist_fw = canonicalize_framework(load_json(NIST_EXPORT_PATH))
    else:
        logger.warning("NIST export not found at %s; cross-mapping + eval demos will be skipped.", NIST_EXPORT_PATH)

    flat_controls = FrameworkNormalizer.flat_controls(nist_fw) if nist_fw else []

    # ── Step 1: Generate per-framework Market Assessment questionnaires ──
    print_section("Step 1 — Generating Market Assessment questionnaires (per framework, per domain)")

    print("    Refreshing canonical framework JSON exports...")
    save_framework_exports()
    catalog = load_framework_catalog()

    qbuilder = QuestionnaireBuilder(llm_api_key=LLM_API_KEY)

    MA_EXPORT_PATH = OUTPUTS_DIR / "canonical_frameworks" / "market_assessment_canonical.json"

    _fw_paths = {
        "nist": NIST_EXPORT_PATH,
        "iso":  ISO_EXPORT_PATH,
        "cis":  CIS_EXPORT_PATH,
        "market_assessment": MA_EXPORT_PATH,
    }

    # Include any dynamically ingested frameworks saved as framework_*.json
    _canonical_dir = OUTPUTS_DIR / "canonical_frameworks"
    if _canonical_dir.exists():
        for _dpath in sorted(_canonical_dir.glob("framework_*.json")):
            _fw_paths[_dpath.stem] = _dpath

    all_individual_q: list = []

    for fw_key, fw_path in _fw_paths.items():
        if not fw_path.exists():
            logger.warning("Skipping %s — export file not found.", fw_key)
            continue

        canonical_fw = canonicalize_framework(load_json(fw_path))
        fw_display    = canonical_fw.get("name", fw_key)
        print(f"\n    ── Framework: {fw_display} ──")

        for domain in canonical_fw.get("domains", []):
            domain_id   = domain.get("domain_id", "")
            domain_name = domain.get("domain_name", domain_id)

            if not domain.get("controls"):
                logger.info("  Skipping empty domain %s in %s", domain_id, fw_key)
                continue

            print(f"      Domain [{domain_id}] {domain_name} — generating Part-1 + Part-2 per sub-topic ...")
            try:
                ma_sections = qbuilder.generate_market_assessment_for_domain(
                    framework=canonical_fw,
                    domain_id=domain_id,
                )

                wrapped = {
                    "framework_id":   canonical_fw.get("framework_id", fw_key),
                    "framework_name": fw_display,
                    "domain_id":      domain_id,
                    "domain_name":    domain_name,
                    "sections":       ma_sections,
                    "generated_at":   timestamp(),
                }

                all_individual_q.append(wrapped)

                out_path = OUTPUTS_DIR / f"questionnaire_MA_{fw_key}_{domain_id}.json"
                save_json(wrapped, out_path)

                part1_count = sum(1 for s in ma_sections if s.get("part1_question"))
                part2_count = sum(len(s.get("questions", [])) for s in ma_sections)
                print(f"        ✓ {len(ma_sections)} sub-topic(s)  |  "
                      f"{part1_count} Part-1 question(s)  |  "
                      f"{part2_count} Part-2 question(s)  →  {out_path.name}")

            except Exception as exc:
                logger.warning(
                    "Market assessment generation failed for %s / %s: %s",
                    fw_key, domain_id, exc,
                )

    print(f"\n    ✓ Total framework questionnaire packages generated: {len(all_individual_q)}")
    save_json(
        [{"framework_id": q["framework_id"], "domain_id": q["domain_id"],
          "subtopics": len(q["sections"])} for q in all_individual_q],
        OUTPUTS_DIR / "questionnaire_manifest.json",
    )
    print(f"    ✓ Manifest saved → {OUTPUTS_DIR / 'questionnaire_manifest.json'}")

    # ── Step 2: Cross-framework mapping ─────────────────────────────────
    print_section("Step 2 — Cross-framework mapping (CSF 2.0 → CSF 1.1)")
    mappings: list = []
    if nist_fw:
        mapper   = CrossFrameworkMapper(threshold=0.55)
        mappings = mapper.map_framework(nist_fw, top_k=2)
        CrossFrameworkMapper.print_mappings(mappings, limit=25)
        print(f"\n    Total mappings : {len(mappings)}")
        mappings_output = OUTPUTS_DIR / "cross_mappings.json"
        save_json(mappings, mappings_output)
        print(f"    ✓ Cross-mappings saved → {mappings_output}")
    else:
        print("    (skipped — NIST export not available)")

    # ── Step 3: RAG Q&A demo ─────────────────────────────────────────────
    print_section("Step 3 — RAG Pipeline: Free-form Q&A")
    rag = RAGPipeline(vectordb=vectordb, llm_api_key=LLM_API_KEY, k=5)

    demo_queries = [
        "What NIST CSF controls apply to multi-factor authentication?",
        "Which controls cover data encryption at rest and in transit?",
    ]

    for query in demo_queries:
        print(f"\n  Query: {query}")
        result = rag.ask(query)
        RAGPipeline.print_answer(result)

    # ── Step 4: RAG questionnaire generation ─────────────────────────────
    print_section("Step 4 — RAG Pipeline: Topic-based Questionnaire Generation")
    topics = [
        "Access control and identity management assessment",
        "Incident detection and monitoring readiness",
    ]
    rag_questionnaires: list = []
    for topic in topics:
        print(f"\n  Topic: {topic}")
        q = rag.generate(topic)
        RAGPipeline.print_questionnaire(q)
        rag_questionnaires.append(q)

    save_json(rag_questionnaires, OUTPUTS_DIR / "rag_questionnaires.json")
    print(f"\n    ✓ RAG questionnaires saved → {OUTPUTS_DIR / 'rag_questionnaires.json'}")

    # ── Step 5: Evidence evaluation ──────────────────────────────────────
    print_section("Step 5 — Evidence Evaluation")
    print(f"    Evidence file: {ev_path}")
    print()

    if flat_controls:
        eval_controls = flat_controls[:8]
        evaluator     = EvidenceEvaluator(
            llm_api_key = LLM_API_KEY,
            vectordb    = vectordb,
            chunker     = chunker,
        )

        verdicts = evaluator.evaluate_file(
            evidence_path = ev_path,
            controls      = eval_controls,
            top_k         = 4,
        )

        print(f"\n  {'─'*56}")
        print(f"  COMPLIANCE VERDICTS")
        print(f"  {'─'*56}")
        for v in verdicts:
            print_verdict(v)

        summary = EvidenceEvaluator.summarise(verdicts)
        print(f"\n  {'─'*56}")
        print(f"  SUMMARY")
        print(f"  {'─'*56}")
        print_dict(summary, indent=4)

        verdicts_output = OUTPUTS_DIR / "evidence_verdicts.json"
        save_json(
            {"verdicts": verdicts, "summary": summary, "evaluated_at": timestamp()},
            verdicts_output,
        )
        print(f"\n    ✓ Verdicts saved → {verdicts_output}")
    else:
        verdicts = []
        summary  = {}
        print("    (skipped — NIST export not available for control list)")

    # ── Final summary ─────────────────────────────────────────────────────
    print_banner("Pipeline Complete")
    fw_label = nist_fw.get("name", "N/A") if nist_fw else "N/A"
    print(f"    Framework demo : {fw_label}")
    print(f"    Total controls : {len(flat_controls)}")
    total_subtopics = sum(len(q.get("sections", [])) for q in all_individual_q)
    print(f"    MA packages    : {len(all_individual_q)} (framework×domain) / {total_subtopics} sub-topics")
    print(f"    Cross-mappings : {len(mappings)}")
    print(f"    Verdicts       : {len(verdicts)}")
    print(f"    Outputs        : {OUTPUTS_DIR.resolve()}")
    print()


if __name__ == "__main__":
    main()
