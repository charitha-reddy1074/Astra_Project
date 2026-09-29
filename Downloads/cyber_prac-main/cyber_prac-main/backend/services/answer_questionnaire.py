"""
Interactive questionnaire answerer.

Usage:
    python answer_questionnaire.py [path/to/questionnaire.json]

If no path provided, defaults to `data/outputs/questionnaire_neo4j.json`.
Saves answers to `data/outputs/questionnaire_answers_<control_id>.json`.
Attempts to persist answers to Neo4j using the Bolt driver or REST fallback.
"""
from pathlib import Path
import json
import os
import re
from datetime import datetime
import requests

from backend.core.utils import LLMClient, logger, parse_llm_json
from backend.exporters.report_generator import build_report_data
from backend.core.evaluation_metrics import (
    get_framework_profile,
    score_evidence_quality,
    apply_autoflag_rules,
    market_control_rollup,
    CRITICALITY_MULT,
)

try:
    from neo4j import GraphDatabase
    from neo4j import basic_auth
except Exception:
    GraphDatabase = None
from typing import Any, Dict, List, Optional

import backend.config.settings  # noqa: F401  (loads backend/.env once)

QUESTIONNAIRE_DEFAULT = Path("data/outputs/questionnaire_neo4j.json")

SCORE_SYSTEM_PROMPT = """\
You are a senior cybersecurity compliance auditor reviewing questionnaire answers
and the evidence submitted to support those answers.

You will be given a list of questions, each with the submitted answer, optional
notes, and an evidence_preview (text extracted from uploaded files for that question).

Your task: score each answer for quality, completeness, and credibility — and
explicitly check whether the evidence_preview actually supports the stated answer.

SCORING GUIDANCE:
- Score only what was submitted. Do not award credit for assumed or common practices.
- A strong answer is specific, consistent, and directly tied to the question criteria.
- A vague, missing, or irrelevant answer should receive a low score.
- Use the question type, maturity_signals, and gap_if_deficient when present.

EVIDENCE-ANSWER ALIGNMENT (apply whenever evidence_preview is provided):
Compare what the answer CLAIMS against what the evidence SHOWS and set evidence_alignment:
  "direct"       — evidence explicitly confirms the claimed control/practice → no cap
  "partial"      — evidence partially supports the claim (implied, not explicitly proven) → cap score at 75
  "weak"         — evidence is generic; not clearly linked to this specific control → cap score at 55
  "contradiction"— answer claims X but evidence shows no X or the opposite → cap score at 35
  "none"         — no evidence_preview or evidence is completely unrelated

Policy templates, blank framework docs, or standards without deployment data are "weak"
evidence regardless of what the answer claims. Operational evidence (logs, reports,
dashboards, timestamps, tickets) supports higher scores.

MATURITY CLASSIFICATION:
- When maturity_signals are present, classify the answer as mature / partial / critical_gap.
- Keep score consistent with category: mature ≈ 75-100, partial ≈ 40-74, critical_gap ≈ 0-39.
- When the answer is NOT mature, set "gap" to the concrete deficiency revealed
  (use gap_if_deficient if supplied, else describe the specific gap you observed).

Return ONLY valid JSON with this exact structure:
{
    "summary": "<1-3 sentence explanation of overall findings>",
    "strengths": ["<strength>", "..."],
    "gaps": ["<gap>", "..."],
    "question_reviews": [
        {
            "question_id": "<id>",
            "verdict": "<strong | partial | weak | missing>",
            "score": <0 to 100 number>,
            "comment": "<brief justification including evidence alignment reasoning>",
            "evidence_alignment": "<direct | partial | weak | none | contradiction>",
            "maturity_category": "<mature | partial | critical_gap>",
            "gap": "<specific gap if not mature, else empty string>"
        }
    ]
}

Be strict, evidence-based, and flag every evidence-answer contradiction in the comment.
"""

# Human-prompt template used by _llm_score_in_memory (in-memory path, no file I/O).
_SCORE_HUMAN_PROMPT_TEMPLATE = """\
=== ASSESSMENT ===
Questionnaire ID : {questionnaire_id}
Framework        : {framework_name}
Scope            : {scope_name}

=== QUESTIONS WITH ANSWERS AND EVIDENCE ===
Each entry contains: question text, submitted answer (response), optional notes,
and evidence_preview (text extracted from files uploaded for that question).
Check every evidence_preview against the stated answer and apply the alignment rules.

{questions_and_answers_json}

=== RULES ===
- Return one entry in question_reviews for EVERY question_id listed above.
- When evidence_preview is non-empty, determine if it directly supports the answer.
- Flag contradictions: if the answer says "yes we have X" but evidence shows no X,
  set evidence_alignment to "contradiction" and score to ≤ 35.
- Prefer conservative scores over speculative credit.

Return the JSON scoring object now.
"""

SCORE_HUMAN_PROMPT = """\
=== QUESTIONNAIRE ===
Questionnaire ID : {{ questionnaire_id }}
Control ID       : {{ control_id }}
Domain ID        : {{ domain_id }}
Framework        : {{ framework_name }}
Scope            : {{ scope_name }}

Questions:
{{ questions_json }}

=== ANSWERS ===
{{ answers_json }}

=== SCORING RULES ===
- A question review must judge the answer against the question text and expected evidence.
- If the answer is an uploaded PDF or other file that is just a framework/standard
    document, score it as irrelevant unless it clearly demonstrates implementation.
- If the response does not address the question or the evidence does not support it,
    score it low.
- Prefer low scores over speculative credit.

Review the submitted answers and assign the final score.
"""

def prompt_input(prompt, valid=None, default=None):
    while True:
        v = input(f"{prompt}" + (f" [{default}]" if default else "") + ": ")
        if not v and default is not None:
            return default
        if not valid:
            return v
        if v in valid:
            return v
        print(f"Invalid choice — expected one of: {valid}")


def load_questionnaire(path: Path):
    if not path.exists():
        raise SystemExit(f"Questionnaire not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def collect_answers(q):
    answers = {
        'questionnaire_id': q.get('questionnaire_id') or q.get('control_id'),
        'control_id': q.get('control_id'),
        'domain_id': q.get('domain_id'),
        'domain_name': q.get('domain_name'),
        'domain_description': q.get('domain_description'),
        'control_statement': q.get('control_statement'),
        'answers': [],
        'answered_at': datetime.utcnow().isoformat() + 'Z',
    }
    scope = q.get('domain_name') or q.get('control_id')
    print('\nAnswering questionnaire for', scope)
    name = input('Your name (auditor): ').strip() or 'auditor'
    answers['auditor'] = name

    for i, question in enumerate(q.get('questions', []), 1):
        print('\n')
        print(f"Q{i}. {question.get('question_text')}")
        qtype = question.get('question_type', 'free_text')
        resp = None
        if qtype == 'yes_no':
            resp = prompt_input('Answer (yes/no)', valid=('yes','no'))
        elif qtype == 'maturity_rating':
            options = ('Initial','Developing','Defined','Managed','Optimizing')
            # Case-insensitive acceptance; normalize to canonical form
            while True:
                raw = input(f'Maturity ({"/".join(options)}): ').strip()
                if not raw:
                    resp = options[0]
                    break
                norm = raw.capitalize()
                if norm in options:
                    resp = norm
                    break
                print(f"Invalid choice — expected one of: {options}")
        elif qtype == 'evidence_upload':
            p = prompt_input('Path to evidence file (leave empty if none)', default='')
            resp = p
        else:
            resp = input('Answer (free text): ')

        note = input('Optional note/context (enter to skip): ')

        answers['answers'].append({
            'question_id': question.get('question_id'),
            'question_text': question.get('question_text'),
            'question_type': qtype,
            'response': resp,
            'note': note,
            'responded_at': datetime.utcnow().isoformat() + 'Z',
        })
    return answers


def save_answers(ans):
    outdir = Path('data/outputs')
    outdir.mkdir(parents=True, exist_ok=True)
    qid = ans.get('questionnaire_id') or ans.get('control_id', 'unknown')
    out = outdir / f'questionnaire_answers_{qid}.json'
    out.write_text(json.dumps(ans, indent=2), encoding="utf-8")
    print('Answers saved to', out)
    return out


def _parse_model_json(raw: str) -> dict:
    return parse_llm_json(raw)


def _render_template(template: str, values: Dict[str, Any]) -> str:
    return re.sub(r"{{\s*(\w+)\s*}}", lambda m: str(values.get(m.group(1), '')), template)


def _build_score_chain(llm_api_key: str, model: str = 'llama-3.3-70b-versatile'):
    """Builds an LLM chain for scoring using the centralized LLMClient."""
    if not llm_api_key:
        return None
    # The LLMClient's invoke method expects system_prompt and user_prompt directly
    # We'll wrap it to match the expected chain interface for this function
    client = LLMClient(llm_api_key, model, temperature=0.1)
    return client


def _detect_framework_key(questionnaire: dict) -> str:
    """Derive a normalised framework key from questionnaire metadata."""
    for field in ('framework_id', 'framework_name', 'framework_key', 'framework_summary'):
        val = str(questionnaire.get(field) or '').lower()
        if val:
            if 'market' in val:
                return 'market-assessment'
            if 'iso' in val or '27001' in val:
                return 'iso-27001-2022'
            if 'cis' in val:
                return 'cis-controls-v8-1-2'
            if 'nist' in val or 'csf' in val:
                return 'nist-csf-2-0'
    fw_names = questionnaire.get('framework_names') or []
    joined = ' '.join(str(n).lower() for n in fw_names)
    if 'market' in joined:
        return 'market-assessment'
    if 'iso' in joined or '27001' in joined:
        return 'iso-27001-2022'
    if 'cis' in joined:
        return 'cis-controls-v8-1-2'
    return 'nist-csf-2-0'


_QTYPE_NORM = {
    'YES_NO': 'yes_no',
    'SCALE_1_5': 'scale_1_5',
    'MULTI_CHOICE': 'multi_choice',
    'FREE_TEXT': 'free_text',
    'EVIDENCE_UPLOAD': 'evidence_upload',
}

# ── Maturity-signal classification ───────────────────────────────────────────
# Questions generated from the framework's maturity guides carry per-question
# maturity_signals (what a Mature / Partial / Critical-Gap answer looks like) and
# gap_if_deficient (the gap a weak answer reveals). The scorer classifies each
# answer onto this Mature / Partial / Critical-Gap ladder — the framework's own
# terms — and rolls the classifications up per control and overall.
_MATURITY_CAT_LABELS = {
    'mature': 'Mature',
    'partial': 'Partial',
    'critical_gap': 'Critical Gap',
}


def _maturity_category(score: Any) -> str:
    """Map a 0–100 question score onto the Mature/Partial/Critical-Gap ladder.

    Bands mirror the verdict thresholds used elsewhere (strong/partial/weak).
    """
    try:
        s = float(score or 0)
    except (TypeError, ValueError):
        s = 0.0
    if s >= 75:
        return 'mature'
    if s >= 40:
        return 'partial'
    return 'critical_gap'


def _attach_maturity_fields(review: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
    """Attach maturity_category / expectation / gap to a question review in-place.

    Uses an explicit category if already present (e.g. from the LLM), otherwise
    derives it from the numeric score. The expectation is the rubric wording for
    the chosen category; the gap is gap_if_deficient (or the critical-gap rubric)
    whenever the answer is not Mature.
    """
    signals = meta.get('maturity_signals') or review.get('maturity_signals') or {}
    cat = str(review.get('maturity_category') or '').strip().lower()
    if cat not in _MATURITY_CAT_LABELS:
        cat = _maturity_category(review.get('score'))
    review['maturity_category'] = cat
    review['maturity_category_label'] = _MATURITY_CAT_LABELS[cat]
    review['maturity_expectation'] = signals.get(cat, '')
    if cat == 'mature':
        review['gap'] = ''
    else:
        review['gap'] = (
            review.get('gap')
            or meta.get('gap_if_deficient')
            or signals.get('critical_gap', '')
        )
    if not review.get('sub_topic'):
        review['sub_topic'] = meta.get('sub_topic', '')
    return review


def _rollup_maturity(reviews: List[Dict[str, Any]]) -> tuple:
    """Roll per-question maturity categories up per control and overall.

    Returns (breakdown, identified_gaps, classification) where:
      - breakdown : per-control {control_id, sub_topic, score, category, label, gaps}
      - identified_gaps : deduped list of gaps across all non-mature questions
      - classification  : overall {category, category_label, counts}
    """
    by_control: Dict[str, Dict[str, Any]] = {}
    identified_gaps: List[str] = []
    seen_gaps: set = set()
    counts = {'mature': 0, 'partial': 0, 'critical_gap': 0}

    for r in reviews:
        cat = str(r.get('maturity_category') or '').strip().lower()
        if cat not in _MATURITY_CAT_LABELS:
            cat = _maturity_category(r.get('score'))
        counts[cat] += 1
        cid = str(r.get('control_id') or '—')
        entry = by_control.setdefault(cid, {
            'control_id': cid,
            'sub_topic': r.get('sub_topic', ''),
            'scores': [],
            'gaps': [],
        })
        if not entry['sub_topic'] and r.get('sub_topic'):
            entry['sub_topic'] = r.get('sub_topic')
        entry['scores'].append(float(r.get('score') or 0))
        if cat != 'mature':
            g = str(r.get('gap') or '').strip()
            if g and g not in seen_gaps:
                seen_gaps.add(g)
                identified_gaps.append(g)
            if g and g not in entry['gaps']:
                entry['gaps'].append(g)

    breakdown: List[Dict[str, Any]] = []
    for cid, e in by_control.items():
        avg = sum(e['scores']) / len(e['scores']) if e['scores'] else 0.0
        cat = _maturity_category(avg)
        breakdown.append({
            'control_id': cid,
            'sub_topic': e['sub_topic'],
            'score': round(avg, 1),
            'category': cat,
            'category_label': _MATURITY_CAT_LABELS[cat],
            'gaps': e['gaps'],
        })
    breakdown.sort(key=lambda b: b['score'])

    total = sum(counts.values()) or 1
    if counts['critical_gap'] / total >= 0.34:
        sig = 'critical_gap'
    elif counts['mature'] / total >= 0.67:
        sig = 'mature'
    else:
        sig = 'partial'
    classification = {
        'category': sig,
        'category_label': _MATURITY_CAT_LABELS[sig],
        'counts': counts,
    }
    return breakdown, identified_gaps, classification


def _all_scorable_questions(questionnaire: dict) -> list:
    """Return all questions that were rendered in the form (mirrors app._get_form_questions)."""
    if questionnaire.get('questionnaire_type') == 'multi_framework':
        result, seen = [], set()
        for group in questionnaire.get('question_groups', []):
            domain_id = group.get('domain_id', '')
            group_added = 0
            for section in group.get('sub_topics', []):
                for sq in section.get('questions', []):
                    qid = sq.get('question_id')
                    if not qid or qid in seen:
                        continue
                    seen.add(qid)
                    raw = (sq.get('question_type') or 'FREE_TEXT').upper()
                    result.append({
                        'question_id': qid,
                        'question_text': sq.get('text') or sq.get('question_text', ''),
                        'question_type': _QTYPE_NORM.get(raw, 'free_text'),
                        'weight': sq.get('weight', 3),
                        'control_id': sq.get('control_id', ''),
                        'domain_id': domain_id,
                        'sub_topic': sq.get('sub_topic', section.get('sub_topic', '')),
                        'maturity_signals': sq.get('maturity_signals', {}),
                        'gap_if_deficient': sq.get('gap_if_deficient', ''),
                    })
                    group_added += 1

            # Fallback: groups with no sub-topic questions render their own
            # (merged/flat) questions in the form — score those too so the
            # weighted denominator covers every shown question.
            if group_added == 0:
                for gq in group.get('questions', []) or []:
                    qid = gq.get('question_id')
                    if not qid or qid in seen:
                        continue
                    seen.add(qid)
                    raw = (gq.get('question_type') or 'free_text').upper()
                    result.append({
                        'question_id': qid,
                        'question_text': gq.get('text') or gq.get('question_text', ''),
                        'question_type': _QTYPE_NORM.get(raw, gq.get('question_type', 'free_text')),
                        'weight': gq.get('weight', 3),
                        'control_id': gq.get('control_id', ''),
                        'domain_id': gq.get('domain_id', domain_id),
                        'sub_topic': gq.get('sub_topic', ''),
                        'maturity_signals': gq.get('maturity_signals', {}),
                        'gap_if_deficient': gq.get('gap_if_deficient', ''),
                    })
        return result
    return questionnaire.get('questions', [])


def _deterministic_fallback_score(questionnaire: dict, answers: dict) -> dict:
    fw_key = _detect_framework_key(questionnaire)
    # Build lookup from both flat questions and sub-topic questions
    qmap = {qq.get('question_id'): qq for qq in _all_scorable_questions(questionnaire)}
    per_q = []

    for a in answers.get('answers', []):
        qid = a.get('question_id')
        meta = qmap.get(qid, {})
        # Normalise type — handle uppercase from sub-topic questions
        raw_type = (a.get('question_type') or 'free_text').upper()
        qtype = _QTYPE_NORM.get(raw_type, a.get('question_type', 'free_text').lower())
        resp = a.get('response')
        evidence_preview = a.get('evidence_preview', '')

        if qtype == 'yes_no':
            score = 100.0 if str(resp or '').lower() in ('yes', 'y', 'true', '1') else 0.0
            verdict = 'strong' if score == 100.0 else 'weak'
            comment = 'Yes/No answer scored: Yes=100, No=0.'
            alignment = 'direct' if score == 100.0 else 'none'

        elif qtype == 'scale_1_5':
            try:
                val = int(str(resp or '0'))
            except (ValueError, TypeError):
                val = 0
            # Map 1..5 → 20..100 so a valid lowest rating is not treated as a
            # non-answer (mirrors the maturity_rating scale); 0/invalid → 0.
            score = (20.0 + (val - 1) * 20.0) if 1 <= val <= 5 else 0.0
            verdict = 'strong' if score >= 75 else 'partial' if score >= 40 else 'weak'
            comment = f'Scale answer {val}/5 → {score:.0f}%.'
            alignment = 'direct' if score >= 75 else 'partial'

        elif qtype == 'maturity_rating':
            maturity_scale = {
                'Initial': 20.0, 'Developing': 40.0, 'Defined': 60.0, 'Managed': 80.0, 'Optimizing': 100.0,
                # Organizational Assessment 0-3 scale labels.
                'Not in place': 0.0, 'Partially in place': 33.33, 'Passable': 66.67, 'Strong': 100.0,
                '0': 0.0, '1': 33.33, '2': 66.67, '3': 100.0,
            }
            score = maturity_scale.get(str(resp or '').strip(), 0.0)
            verdict = 'strong' if score >= 80.0 else 'partial' if score >= 40.0 else 'weak'
            comment = f'Maturity level "{resp}" → {score:.0f}%.'
            alignment = 'direct' if score >= 80.0 else 'partial'

        elif qtype == 'multi_choice':
            choice = str(resp or '').strip().lower()
            _MARKET_MC = {
                'not in place': 0.0, 'partially in place': 33.33,
                'passable': 66.67, 'strong': 100.0,
                # Numeric 0-3 anchors map identically (AI prefill / numeric entry).
                '0': 0.0, '1': 33.33, '2': 66.67, '3': 100.0,
            }
            if choice in _MARKET_MC:
                # Organizational Assessment 0-3 maturity choice.
                score = _MARKET_MC[choice]
                comment = f'Maturity selection "{resp}" → {score:.0f}% (0-3 scale).'
            elif not choice:
                score = 0.0
                comment = 'Multiple-choice answer unanswered → 0%.'
            elif any(k in choice for k in ('not implement', 'none', 'never', 'absent', 'no evidence', 'n/a')):
                score = 20.0
                comment = f'Multiple-choice selection "{resp}" indicates not implemented → 20%.'
            elif any(k in choice for k in ('partial', 'some', 'in progress', 'planned', 'developing', 'ad hoc')):
                score = 50.0
                comment = f'Multiple-choice selection "{resp}" indicates partial implementation → 50%.'
            elif any(k in choice for k in ('full', 'complete', 'implemented', 'always', 'all ', 'optimiz', 'managed')):
                score = 90.0
                comment = f'Multiple-choice selection "{resp}" indicates implemented → 90%.'
            else:
                score = 70.0
                comment = f'Multiple-choice selection "{resp}" recorded (neutral) → 70%.'
            verdict = 'strong' if score >= 75 else 'partial' if score >= 40 else 'weak' if score else 'missing'
            alignment = 'direct' if score >= 75 else 'partial' if score else 'none'

        elif qtype == 'evidence_upload':
            score = score_evidence_quality(resp, evidence_preview, framework_key=fw_key)
            verdict = 'strong' if score >= 75 else 'partial' if score >= 40 else 'missing'
            comment = f'Evidence quality graded by keyword rubric: {score:.0f}/100.'
            alignment = 'direct' if score >= 75 else 'partial' if score >= 40 else 'none'

        else:  # free_text
            resp_str = str(resp or '').strip()
            words = len(resp_str.split()) if resp_str else 0
            if not words:
                score = 0.0
                comment = 'Free-text answer left blank.'
            else:
                # Graded on the SAME discrete 0/20/40/75/100 rubric as
                # evidence_upload so granularity is consistent across question
                # types, and with a hard 100% ceiling (no unbounded word-count
                # formula). Length and question relevance (word overlap) each
                # select a tier rather than scaling a continuous multiplier.
                q_text = str(meta.get('question_text') or a.get('question_text') or '')
                q_words = set(re.findall(r'\w+', q_text.lower()))
                r_words = set(re.findall(r'\w+', resp_str.lower()))
                overlap = len(q_words & r_words) / max(len(q_words), 1)
                if words >= 40 and overlap >= 0.30:
                    score = 100.0   # comprehensive and on-topic
                elif words >= 20 and overlap >= 0.15:
                    score = 75.0    # substantive and relevant
                elif words >= 10:
                    score = 40.0    # partial detail
                else:
                    score = 20.0    # minimal detail
                comment = (
                    f'Free-text graded on the 0/20/40/75/100 rubric by length '
                    f'({words} words) and relevance (overlap {overlap:.0%}) → '
                    f'{score:.0f}%.'
                )
            verdict = 'strong' if score >= 75 else 'partial' if score >= 40 else 'weak' if score else 'missing'
            alignment = 'direct' if score >= 75 else 'partial' if score else 'none'

        # 50/50 blend: when evidence accompanies an answered question, the final
        # score is half the answer score, half the graded evidence quality.
        # With no evidence attached, the answer alone counts (no penalty).
        # Exception: a YES/NO answered "NO" must NOT be boosted — the assessor
        # explicitly said the control is absent; crediting evidence here inflates
        # confidence on something they said doesn't exist.
        _yes_no_negative = (
            qtype == 'yes_no'
            and str(resp or '').strip().lower() not in ('yes', 'y', 'true', '1')
        )
        if (
            qtype != 'evidence_upload'
            and str(resp or '').strip()
            and str(evidence_preview or '').strip()
            and not _yes_no_negative
        ):
            ev_score = score_evidence_quality(resp, evidence_preview, framework_key=fw_key)
            score = round(0.5 * score + 0.5 * ev_score, 2)
            verdict = 'strong' if score >= 75 else 'partial' if score >= 40 else 'weak' if score else 'missing'
            alignment = 'direct' if score >= 75 else 'partial' if score else 'none'
            comment += (
                f' Evidence graded {ev_score:.0f}/100 → final score is '
                f'50% answer + 50% evidence.'
            )

        review = {
            'question_id': qid,
            'question_text': a.get('question_text'),
            'question_type': qtype,
            'control_id': meta.get('control_id', a.get('control_id', '')),
            'domain_id': meta.get('domain_id', a.get('domain_id', questionnaire.get('domain_id', ''))),
            'sub_topic': meta.get('sub_topic', ''),
            'response': resp,
            'verdict': verdict,
            'score': round(score, 2),
            'comment': comment,
            'evidence_alignment': alignment,
            'evidence_preview': evidence_preview,
            'weight': meta.get('weight', a.get('weight', 3)),
        }
        # Classify the answer onto the framework's Mature/Partial/Critical-Gap
        # ladder using this question's maturity_signals, and record the gap.
        _attach_maturity_fields(review, meta)
        per_q.append(review)

    return _finalize_scoring(
        per_q, questionnaire,
        scoring_mode='fallback-deterministic',
        summary='Deterministic score computed from submitted answers.',
        strengths=[], gaps=[],
        framework_key=fw_key,
    )


def _finalize_scoring(
    question_reviews: list[dict],
    questionnaire: dict,
    scoring_mode: str,
    summary: str,
    strengths: list[str],
    gaps: list[str],
    framework_key: str = None,
) -> dict:
    if not framework_key:
        framework_key = _detect_framework_key(questionnaire)

    profile = get_framework_profile(framework_key, questionnaire_data=questionnaire)
    domain_weights: dict = profile.get('domain_weights', {})
    criticality_fn = profile.get('criticality_fn', lambda _: 'standard')

    qmap = {qq.get('question_id'): qq for qq in questionnaire.get('questions', [])}
    total_weight = 0.0
    total_weighted = 0.0

    for review in question_reviews:
        raw_score = review.get('score')
        if raw_score is None:
            continue

        qid = review.get('question_id')
        q_meta = qmap.get(qid, {})

        base_weight = float(q_meta.get('weight', review.get('weight', 3)))

        # Layer 1: domain weight multiplier
        domain_id = str(
            review.get('domain_id') or q_meta.get('domain_id') or questionnaire.get('domain_id') or ''
        ).upper()
        domain_key = domain_id.split('-')[0]
        dw = domain_weights.get(domain_id, domain_weights.get(domain_key, 3.0))

        # Layer 2: criticality tier multiplier
        control_id = review.get('control_id') or q_meta.get('control_id', '')
        tier = criticality_fn(control_id)
        cm = CRITICALITY_MULT.get(tier, 1.0)

        effective_weight = base_weight * (dw / 3.0) * cm

        score = float(raw_score)
        total_weight += effective_weight
        total_weighted += (score / 100.0) * effective_weight

    control_percent = 0.0 if total_weight <= 0 else (total_weighted / total_weight) * 100.0

    # Layer 3: framework-specific band classification
    risk, maturity = profile['classify'](control_percent)

    # Organizational Assessment: the authoritative overall score is the xlsx
    # achieved/possible roll-up of the per-item 0-3 ratings (not the weighted
    # question average), so the Assessments view and the Reports view match.
    ai_levels: dict = {}
    if framework_key == 'market-assessment':
        # AI rubric-derived per-item levels (anchor still wins when set) — the
        # report builder threads these in as {control_id: level} so the score
        # reflects the Technology/Process rubric rather than an inflated
        # yes/no average.
        ai_levels = questionnaire.get('ai_item_levels') or {}
        _roll = market_control_rollup(question_reviews, ai_levels=ai_levels)
        control_percent = _roll['pct']
        risk, maturity = profile['classify'](control_percent)

    # Layer 4: maturity-signal rollup — classify the org on the framework's own
    # Mature / Partial / Critical-Gap ladder (per control + overall) and collect
    # the concrete gaps that non-mature answers revealed.
    maturity_breakdown, identified_gaps, signal_maturity = _rollup_maturity(question_reviews)

    # Surface the rubric-derived gaps when the narrative gaps list is empty.
    combined_gaps = list(gaps) or list(identified_gaps)

    result = {
        'control_score_percent': round(control_percent, 2),
        'overall_risk_rating': risk,
        'maturity_level': maturity,
        'signal_maturity': signal_maturity,
        'maturity_breakdown': maturity_breakdown,
        'identified_gaps': identified_gaps,
        'summary': summary,
        'strengths': strengths,
        'gaps': combined_gaps,
        'question_reviews': question_reviews,
        'scoring_mode': scoring_mode,
        'framework_key': framework_key,
        # AI rubric levels used for the market roll-up, so persist paths can
        # re-derive per-domain scores identically to the report.
        'ai_item_levels': ai_levels,
    }

    # Layer 5: auto-flagging rules (mutates result in-place)
    apply_autoflag_rules(result, question_reviews, profile)

    return result


def _llm_score_in_memory(questionnaire: dict, answers: dict, api_key: str = "") -> dict | None:
    """Run LLM scoring on in-memory questionnaire + answers dicts.

    Explicitly checks evidence-answer alignment: the LLM reviews what each
    answer *claims* against the evidence_preview attached to that answer and
    flags mismatches (e.g. "yes we have X" but evidence shows no X).

    Returns a scored result dict on success, or None when the LLM is unavailable
    or fails — callers fall back to _deterministic_fallback_score().
    """
    api_key = api_key or os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return None

    fw_key = _detect_framework_key(questionnaire)
    qmap = {qq.get("question_id"): qq for qq in _all_scorable_questions(questionnaire)}
    answers_list = answers.get("answers") or []
    if not answers_list:
        return None

    rows = []
    for a in answers_list:
        qid = a.get("question_id")
        meta = qmap.get(qid) or {}
        rows.append({
            "question_id": qid,
            "question_text": meta.get("question_text") or a.get("question_text") or "",
            "question_type": meta.get("question_type") or a.get("question_type") or "free_text",
            "control_id": meta.get("control_id") or a.get("control_id") or "",
            "domain_id": meta.get("domain_id") or a.get("domain_id") or "",
            "sub_topic": meta.get("sub_topic") or "",
            "weight": meta.get("weight") or a.get("weight") or 3,
            "maturity_signals": meta.get("maturity_signals") or {},
            "gap_if_deficient": meta.get("gap_if_deficient") or "",
            "response": a.get("response"),
            "note": a.get("note") or "",
            "evidence_preview": (a.get("evidence_preview") or "")[:1500],
        })

    user_prompt = _SCORE_HUMAN_PROMPT_TEMPLATE.format(
        questionnaire_id=answers.get("questionnaire_id") or "",
        framework_name=questionnaire.get("framework_name") or "",
        scope_name=questionnaire.get("domain_name") or questionnaire.get("name") or "",
        questions_and_answers_json=json.dumps(rows, indent=2),
    )

    try:
        client = LLMClient(api_key, temperature=0.1)
        raw = client.invoke(
            system_prompt=SCORE_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=4096,
        )
        parsed = _parse_model_json(raw)
    except Exception as exc:
        logger.warning("LLM scoring call failed: %s", exc)
        return None

    if not isinstance(parsed, dict):
        return None

    llm_reviews = parsed.get("question_reviews") or []
    if not llm_reviews:
        return None

    row_by_id = {r["question_id"]: r for r in rows if r.get("question_id")}
    question_reviews: list = []
    reviewed_ids: set = set()

    for rev in llm_reviews:
        qid = rev.get("question_id")
        if not qid:
            continue
        row = row_by_id.get(qid) or {}
        raw_type = (row.get("question_type") or "free_text").upper()
        built = {
            "question_id": qid,
            "question_text": row.get("question_text") or "",
            "question_type": _QTYPE_NORM.get(raw_type, raw_type.lower()),
            "response": row.get("response"),
            "verdict": str(rev.get("verdict") or "weak"),
            "score": max(0.0, min(100.0, float(rev.get("score") or 0.0))),
            "comment": str(rev.get("comment") or ""),
            "evidence_alignment": str(rev.get("evidence_alignment") or "none"),
            "control_id": row.get("control_id") or "",
            "domain_id": row.get("domain_id") or "",
            "sub_topic": row.get("sub_topic") or "",
            "weight": row.get("weight") or 3,
            "evidence_preview": row.get("evidence_preview") or "",
            "maturity_category": str(rev.get("maturity_category") or ""),
            "gap": str(rev.get("gap") or ""),
        }
        _attach_maturity_fields(built, row)
        question_reviews.append(built)
        reviewed_ids.add(qid)

    # Backfill questions the LLM omitted so the weighted denominator is complete.
    for row in rows:
        qid = row.get("question_id")
        if not qid or qid in reviewed_ids:
            continue
        raw_type = (row.get("question_type") or "free_text").upper()
        built = {
            "question_id": qid,
            "question_text": row.get("question_text") or "",
            "question_type": _QTYPE_NORM.get(raw_type, raw_type.lower()),
            "response": row.get("response"),
            "verdict": "missing",
            "score": 0.0,
            "comment": "Not reviewed by LLM; scored as missing.",
            "evidence_alignment": "none",
            "control_id": row.get("control_id") or "",
            "domain_id": row.get("domain_id") or "",
            "sub_topic": row.get("sub_topic") or "",
            "weight": row.get("weight") or 3,
            "evidence_preview": row.get("evidence_preview") or "",
        }
        _attach_maturity_fields(built, row)
        question_reviews.append(built)

    return _finalize_scoring(
        question_reviews, questionnaire,
        scoring_mode="llm-review",
        summary=str(parsed.get("summary") or ""),
        strengths=list(parsed.get("strengths") or []),
        gaps=list(parsed.get("gaps") or []),
        framework_key=fw_key,
    )


def try_persist_to_neo4j(ans) -> bool:
    uri = os.getenv('NEO4J_URI')
    user = os.getenv('NEO4J_USER') or os.getenv('NEO4J_USERNAME')
    password = os.getenv('NEO4J_PASSWORD') or os.getenv('NEO4J_PASS')
    database = os.getenv('NEO4J_DATABASE')

    # Try Bolt driver first
    if GraphDatabase and uri and user and password:
        try:
            driver = GraphDatabase.driver(uri, auth=basic_auth(user, password))
            with driver.session(database=database) if database else driver.session() as s:
                # create a QuestionnaireAnswer node and link to Control
                stmt = (
                    "MERGE (c:Control {id: $control_id})\n"
                    "CREATE (a:QuestionnaireAnswer {control_id: $control_id, auditor: $auditor, answered_at: $answered_at})\n"
                    "WITH c,a\n"
                    "MERGE (c)-[:HAS_ANSWER]->(a)"
                )
                s.run(stmt, control_id=ans.get('control_id'), auditor=ans.get('auditor'), answered_at=ans.get('answered_at'))
            logger.info('Persisted answers via Bolt driver')
            return True
        except Exception as e:
            logger.warning('Bolt persist failed: %s', e)
    # REST fallback
    if not (uri and user and password):
        return False
    try:
        host = uri.split('://',1)[1]
        url = f'https://{host}/db/{database}/tx/commit' if database else f'https://{host}/db/neo4j/tx/commit'
        stmts = []
        cid = ans.get('control_id')
        auditor = ans.get('auditor')
        answered_at = ans.get('answered_at')
        stmts.append(f"MERGE (c:Control {{id: '{cid}'}})")
        stmts.append(f"CREATE (a:QuestionnaireAnswer {{control_id: '{cid}', auditor: '{auditor}', answered_at: '{answered_at}'}})")
        stmts.append(f"MATCH (c:Control {{id:'{cid}'}}),(a:QuestionnaireAnswer {{control_id:'{cid}'}}) MERGE (c)-[:HAS_ANSWER]->(a)")
        payload = {"statements": [{"statement": s} for s in stmts]}
        resp = requests.post(url, auth=(user, password), json=payload, timeout=30)
        logger.info('REST persist: %s %s', resp.status_code, resp.reason)
        return resp.status_code == 200
    except Exception as e:
        logger.warning('REST persist failed: %s', e)
    return False


def score_answers(questionnaire_path: Path, answers_path: Path) -> dict:
    """Score the saved answers using an LLM review, with a deterministic fallback.

    Returns the augmented answers dict with scoring details.
    """
    questionnaire_path = Path(questionnaire_path)
    answers_path = Path(answers_path)
    q = json.loads(questionnaire_path.read_text(encoding="utf-8"))
    ans = json.loads(answers_path.read_text(encoding="utf-8"))

    question_rows = []
    # Try to initialise VectorDB to retrieve evidence chunks for each question
    vdb = None
    try:
        # Lazy import: the vector store pulls heavy optional deps (langchain_chroma)
        # that aren't needed for deterministic scoring / report building.
        from backend.data_access.vectordb import VectorDBManager
        vdb = VectorDBManager()
    except Exception:
        vdb = None

    for question in _all_scorable_questions(q):
        qid = question.get('question_id')
        matching_answer = next((a for a in ans.get('answers', []) if a.get('question_id') == qid), {})
        # Retrieve evidence chunks from VectorDB to ground scoring (best-effort)
        retrieved = []
        try:
            if vdb:
                query_text = f"{question.get('control_id','')} {question.get('question_text','')}"
                docs = vdb.search_evidence(query_text, k=4)
                retrieved = [d.page_content for d in docs]
        except Exception:
            retrieved = []

        question_rows.append({
            'question_id': qid,
            'question_text': question.get('question_text'),
            'question_type': question.get('question_type', 'free_text'),
            'control_id': question.get('control_id', ''),
            'domain_id': question.get('domain_id', q.get('domain_id', '')),
            'sub_topic': question.get('sub_topic', ''),
            'weight': question.get('weight', 3),
            'expected_evidence': question.get('expected_evidence', ''),
            'maturity_signals': question.get('maturity_signals', {}),
            'gap_if_deficient': question.get('gap_if_deficient', ''),
            'response': matching_answer.get('response'),
            'note': matching_answer.get('note', ''),
            'evidence_preview': matching_answer.get('evidence_preview', ''),
            'retrieved_evidence': retrieved,
        })

    llm_api_key = os.getenv('GROQ_API_KEY', '').strip()
    scoring = None
    if llm_api_key:
        try:
            chain = _build_score_chain(llm_api_key)
            if not chain: raise RuntimeError("LLM chain not initialized.")
            raw = chain.invoke({
                'questionnaire_id': q.get('questionnaire_id') or q.get('control_id') or '',
                'control_id': q.get('control_id', ''),
                'domain_id': q.get('domain_id', ''),
                'framework_name': q.get('framework_name') or q.get('framework_summary') or 'N/A',
                'scope_name': q.get('domain_name') or q.get('control_id') or q.get('questionnaire_id') or '',
                'questions_json': json.dumps(question_rows, indent=2),
                'answers_json': json.dumps(ans.get('answers', []), indent=2),
            }, system_prompt=SCORE_SYSTEM_PROMPT) # Pass system prompt explicitly
            scoring = _parse_model_json(raw)
            question_reviews = []
            row_by_id = {row['question_id']: row for row in question_rows}
            for review in scoring.get('question_reviews', []):
                row = row_by_id.get(review.get('question_id'), {})
                built = {
                    'question_id': review.get('question_id'),
                    'question_text': row.get('question_text', ''),
                    'question_type': row.get('question_type', 'free_text'),
                    'response': row.get('response', ''),
                    'verdict': review.get('verdict', 'weak'),
                    'score': float(review.get('score', 0.0)),
                    'comment': review.get('comment', ''),
                    'evidence_alignment': review.get('evidence_alignment', 'none'),
                    'control_id': row.get('control_id', ''),
                    'domain_id': row.get('domain_id', ''),
                    'sub_topic': row.get('sub_topic', ''),
                    'weight': row.get('weight', 3),
                    'maturity_category': review.get('maturity_category', ''),
                    'gap': review.get('gap', ''),
                }
                # Normalise category / expectation / gap against the question's rubric
                _attach_maturity_fields(built, row)
                question_reviews.append(built)
            if not question_reviews:
                raise ValueError('LLM returned no question reviews')
            # Backfill any questions the LLM did not return so the weighted
            # denominator covers every question (otherwise the score is computed
            # over a subset and is inflated/unrepresentative).
            reviewed_ids = {r.get('question_id') for r in question_reviews}
            for row in question_rows:
                if row['question_id'] in reviewed_ids:
                    continue
                missing_review = {
                    'question_id': row['question_id'],
                    'question_text': row.get('question_text', ''),
                    'question_type': row.get('question_type', 'free_text'),
                    'response': row.get('response'),
                    'verdict': 'missing',
                    'score': 0.0,
                    'comment': 'Not reviewed by the LLM; scored as missing.',
                    'evidence_alignment': 'none',
                    'control_id': row.get('control_id', ''),
                    'domain_id': row.get('domain_id', ''),
                    'sub_topic': row.get('sub_topic', ''),
                    'weight': row.get('weight', 3),
                }
                _attach_maturity_fields(missing_review, row)
                question_reviews.append(missing_review)
            scoring = _finalize_scoring(
                question_reviews,
                q,
                scoring_mode='llm-review',
                summary=scoring.get('summary', ''),
                strengths=scoring.get('strengths', []),
                gaps=scoring.get('gaps', []),
                framework_key=_detect_framework_key(q),
            )
        except Exception as exc:
            logger.warning('LLM scoring failed, falling back to deterministic scoring: %s', exc)

    if scoring is None:
        scoring = _deterministic_fallback_score(q, ans)

    per_q = scoring.get('question_reviews', []) or []
    scoring.setdefault('summary', '')
    scoring.setdefault('strengths', [])
    scoring.setdefault('gaps', [])
    scoring.setdefault('overall_risk_rating', 'Medium')
    scoring.setdefault('maturity_level', 'Defined')
    scoring.setdefault('control_score_percent', 0.0)
    scoring.setdefault('signal_maturity', {'category': 'partial', 'category_label': 'Partial', 'counts': {}})
    scoring.setdefault('maturity_breakdown', [])
    scoring.setdefault('identified_gaps', [])

    ans['scoring'] = scoring
    ans['scoring_mode'] = scoring.get('scoring_mode')
    ans['control_score_percent'] = scoring.get('control_score_percent')
    ans['overall_risk_rating'] = scoring.get('overall_risk_rating')
    ans['maturity_level'] = scoring.get('maturity_level')
    # save augmented answers
    answers_path.write_text(json.dumps(ans, indent=2), encoding="utf-8")

    # ── Render Jinja2 dashboard report ────────────────────────────────────────
    report_id = ans.get('questionnaire_id') or ans.get('control_id') or questionnaire_path.stem.replace('questionnaire_', '')
    out_html = answers_path.parent / f"questionnaire_report_{report_id}.html"

    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
        template_dir = Path(__file__).resolve().parents[1] / 'legacy_web' / 'templates'
        env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(['html']),
        )
        template = env.get_template('questionnaire_report.html')
        report_data = build_report_data(q, ans, scoring)
        rendered = template.render(**report_data)
        out_html.write_text(rendered, encoding='utf-8')
    except Exception as exc:
        logger.warning('Jinja2 report render failed (%s); writing fallback HTML.', exc)
        # Minimal fallback so the route always has a file to serve
        score_pct = scoring.get('control_score_percent', 0)
        risk = scoring.get('overall_risk_rating', '')
        maturity = scoring.get('maturity_level', '')
        rows = ''.join(
            f"<tr><td>{i}</td><td>{pq.get('question_text','')}</td>"
            f"<td>{pq.get('response','')}</td><td>{pq.get('verdict','')}</td>"
            f"<td>{pq.get('score','')}</td></tr>"
            for i, pq in enumerate(per_q, 1)
        )
        fallback = (
            f'<html><head><meta charset="utf-8"><title>Report</title></head><body>'
            f'<h1>Report — {report_id}</h1>'
            f'<p>Score: {score_pct}% | Risk: {risk} | Maturity: {maturity}</p>'
            f'<table border="1" cellpadding="6"><tr><th>#</th><th>Question</th>'
            f'<th>Response</th><th>Verdict</th><th>Score</th></tr>{rows}</table>'
            f'</body></html>'
        )
        out_html.write_text(fallback, encoding='utf-8')

    return ans


if __name__ == '__main__':
    import sys
    args = sys.argv[1:]
    if args and args[0] in ('--score-only','--score'):
        # usage: --score-only <questionnaire.json> <answers.json>
        if len(args) >= 3:
            qpath = Path(args[1])
            apath = Path(args[2])
        else:
            qpath = Path(args[1]) if len(args) >= 2 else QUESTIONNAIRE_DEFAULT
            # derive answers path from questionnaire control id
            q = load_questionnaire(qpath)
            qid = q.get('questionnaire_id') or q.get('control_id')
            apath = Path(f"data/outputs/questionnaire_answers_{qid}.json")

        res = score_answers(qpath, apath)
        report_id = res.get('questionnaire_id') or res.get('control_id')
        print('Scoring complete. Report saved to', apath.parent / f'questionnaire_report_{report_id}.html')
        sys.exit(0)

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else QUESTIONNAIRE_DEFAULT
    q = load_questionnaire(path)
    ans = collect_answers(q)
    out = save_answers(ans)
    ok = try_persist_to_neo4j(ans)
    if ok:
        print('Answers persisted to Neo4j')
    else:
        print('Could not persist answers to Neo4j (saved locally)')

