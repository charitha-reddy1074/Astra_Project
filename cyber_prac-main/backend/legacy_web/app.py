from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, jsonify, abort
from pathlib import Path
import os
import sys
import re
# Ensure the project root (the folder containing `backend/`) is importable so
# `from backend... import` works whether launched via `python -m` or directly.
BASE = Path(__file__).resolve().parents[2]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import backend.config.settings  # noqa: F401  (loads backend/.env once)
from backend.exporters.xlsx_exporter import save_questionnaire_xlsx
from backend.core.utils import save_json, load_json, logger, LLMClient, parse_llm_json
from backend.services.answer_questionnaire import score_answers
from backend.data_access.vectordb import VectorDBManager
import werkzeug
from pypdf import PdfReader
from docx import Document as DocxDocument
from backend.services.chunker import HierarchicalChunker
from backend.services.framework_exporter import (
    build_questionnaire_from_selection,
    load_framework_catalog as exported_framework_catalog,
    load_nist_framework as exported_nist_framework,
    save_framework_exports,
)

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET', 'devkey')

OUTPUTS = BASE / 'data' / 'outputs'
EVIDENCE = BASE / 'data' / 'evidence'
UPLOADS = BASE / 'data' / 'uploads'
PART1_QUESTIONS_PATH = OUTPUTS / 'part1_questions.json'
MATURITY_SCORING_PATH = OUTPUTS / 'maturity_scoring_criteria.json'


def load_framework_catalog():
    try:
        save_framework_exports()
    except Exception as exc:
        logger.warning('Framework export refresh failed in web UI: %s', exc)
    return exported_framework_catalog()


def questionnaire_summary(q: dict) -> dict:
    """Return compact metadata for dashboard cards.

    The stored ``questionnaire_type`` is sometimes missing on older banks, which
    previously made them render as a broken ``control`` card. Infer it from the
    structure so every questionnaire links to the right route.
    """
    questionnaire_type = q.get('questionnaire_type')
    if not questionnaire_type:
        if q.get('question_groups'):
            questionnaire_type = 'multi_framework'
        elif q.get('domain_id'):
            questionnaire_type = 'domain'
        else:
            questionnaire_type = 'control'

    if questionnaire_type == 'multi_framework':
        scope_id = q.get('questionnaire_id')
        scope_name = q.get('framework_summary') or ', '.join(q.get('framework_names', [])) or q.get('questionnaire_id')
        description = q.get('assessment_notes') or ''
    else:
        scope_id = q.get('domain_id') or q.get('control_id') or q.get('questionnaire_id')
        scope_name = q.get('domain_name') or q.get('control_id') or q.get('questionnaire_id')
        description = q.get('domain_description') or q.get('control_statement') or ''

    # Count questions robustly: flat list, else question_count, else the
    # questions nested under question_groups / sub_topics.
    question_count = len(q.get('questions', []) or [])
    if not question_count:
        groups = q.get('question_groups', []) or []
        nested = sum(len(g.get('questions', []) or []) for g in groups)
        nested += sum(
            len(s.get('questions', []) or [])
            for g in groups for s in (g.get('sub_topics', []) or [])
        )
        question_count = q.get('question_count') or nested

    return {
        'questionnaire_id': q.get('questionnaire_id') or scope_id,
        'scope_id': scope_id,
        'scope_name': scope_name,
        'description': description,
        'question_count': question_count,
        'type': questionnaire_type,
        'domain_id': q.get('domain_id'),
        'control_id': q.get('control_id'),
        'framework_summary': q.get('framework_summary', ''),
    }


def primary_nist_ref(control: dict) -> str:
    refs = control.get('cross_references', []) or []
    for ref in refs:
        if isinstance(ref, dict):
            fw = str(ref.get('other_framework_id', '')).lower()
            if '800-53' in fw or 'nist' in fw:
                return str(ref.get('other_control_id', ''))
            continue
        ref_text = str(ref)
        if '800-53' in ref_text:
            return ref_text.split('NIST SP 800-53', 1)[-1].strip() or ref_text
    return str(refs[0]) if refs else ''


def domain_question_text(domain_name: str, control_id: str, control_statement: str, question_type: str) -> str:
    stem = f'{domain_name} control {control_id}: {control_statement}'
    if question_type == 'yes_no':
        return f'Is the organization implementing and operating {stem.lower()} as documented?'
    if question_type == 'maturity_rating':
        return f'What maturity level best describes how the organization implements {stem.lower()}?'
    if question_type == 'evidence_upload':
        return f'Upload evidence that demonstrates the organization satisfies {stem.lower()}.'
    return f'Describe how the organization implements {stem.lower()} and how it is evidenced in practice.'


def build_domain_questionnaire(framework: dict, domain_id: str, question_count: int = 10, question_id_prefix: str | None = None) -> dict:
    target_domain_id = domain_id.upper()
    domain = next((d for d in framework.get('domains', []) if d.get('domain_id') == target_domain_id), None)
    if not domain:
        raise ValueError(f'Domain not found: {domain_id}')

    controls = list(domain.get('controls', []))
    if not controls:
        raise ValueError(f'No controls available for domain: {domain_id}')

    domain_name = domain.get('domain_name', target_domain_id)
    domain_description = domain.get('description') or f'{domain_name} domain'
    question_types = ['yes_no', 'free_text', 'evidence_upload', 'maturity_rating']
    questions = []

    for idx in range(question_count):
        control = controls[idx % len(controls)]
        qtype = question_types[idx % len(question_types)]
        control_id = control.get('control_id', 'UNKNOWN')
        statement = control.get('control_statement', '')
        question_id = f'{question_id_prefix or target_domain_id}-{idx + 1}'
        questions.append({
            'question_id': question_id,
            'question_text': domain_question_text(domain_name, control_id, statement, qtype),
            'question_type': qtype,
            'control_id': control_id,
            'nist_800_53_ref': primary_nist_ref(control),
            'expected_evidence': ', '.join(control.get('expected_evidence_types', [])),
            'maturity_level': control.get('maturity_levels', ['Defined'])[0] if control.get('maturity_levels') else 'Defined',
            'risk_description': f'Weak implementation of {control_id} can reduce the effectiveness of the {domain_name} domain.',
            'validation_criteria': 'Review policy, operational evidence, and implementation details for consistency.',
            'weight': 4 if qtype in ('yes_no', 'maturity_rating') else 3,
        })

    return {
        'questionnaire_type': 'domain',
        'questionnaire_id': f'domain-{target_domain_id}',
        'framework_name': framework.get('framework_name', 'NIST CSF 2.0'),
        'domain_id': target_domain_id,
        'domain_name': domain_name,
        'domain_description': domain_description,
        'question_count': question_count,
        'questions': questions,
        'overall_risk_rating': 'Medium',
        'assessment_notes': f'Domain questionnaire generated for {domain_name} testing.',
    }


def build_domain_questionnaire_from_vdb(framework: dict, domain: dict, docs: list, question_count: int = 10, question_id_prefix: str | None = None) -> dict:
    domain_id = domain.get('domain_id', '')
    domain_name = domain.get('domain_name', domain_id)
    domain_description = domain.get('description') or f'{domain_name} domain'

    if not docs:
        raise ValueError(f'No vector DB documents found for domain: {domain_id}')

    question_types = ['yes_no', 'free_text', 'evidence_upload', 'maturity_rating']
    questions = []
    for idx in range(question_count):
        doc = docs[idx % len(docs)]
        meta = doc.metadata or {}
        control_id = meta.get('control_id', 'UNKNOWN')
        control_statement = meta.get('control_statement', '')
        qtype = question_types[idx % len(question_types)]
        question_id = f'{question_id_prefix or domain_id}-{idx + 1}'
        questions.append({
            'question_id': question_id,
            'question_text': domain_question_text(domain_name, control_id, control_statement, qtype),
            'question_type': qtype,
            'control_id': control_id,
            'nist_800_53_ref': meta.get('cross_references', ''),
            'expected_evidence': meta.get('evidence_types', ''),
            'maturity_level': (meta.get('maturity_levels', '') or 'Defined').split(',')[0].strip() if meta.get('maturity_levels') else 'Defined',
            'risk_description': f'Weak implementation of {control_id} can reduce the effectiveness of the {domain_name} domain.',
            'validation_criteria': 'Review the retrieved framework control statement, evidence types, and implementation details for consistency.',
            'weight': 4 if qtype in ('yes_no', 'maturity_rating') else 3,
        })

    return {
        'questionnaire_type': 'domain',
        'questionnaire_id': f'domain-{domain_id}',
        'framework_name': framework.get('framework_name', 'NIST CSF 2.0'),
        'domain_id': domain_id,
        'domain_name': domain_name,
        'domain_description': domain_description,
        'question_count': question_count,
        'questions': questions,
        'overall_risk_rating': 'Medium',
        'assessment_notes': f'Domain questionnaire generated from TryChroma framework documents for {domain_name}.',
    }


def build_multi_framework_questionnaire(frameworks: list[dict], selected_domain_keys: list[str], question_count: int = 10) -> dict:
    return build_questionnaire_from_selection(frameworks, selected_domain_keys)


def questionnaire_file_path(questionnaire_id: str) -> Path:
    # Use exact ID; the exporter no longer applies an extra slugify() during save.
    return OUTPUTS / f'questionnaire_bank_{questionnaire_id}.json'


def load_questionnaire_bank(questionnaire_id: str):
    """Load a questionnaire bank JSON by ID, scanning files if exact match not found."""
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    # 1. Try exact filename match first
    exact = OUTPUTS / f"questionnaire_bank_{questionnaire_id}.json"
    if exact.exists():
        return load_json(exact)

    # 2. Scan all questionnaire_bank_*.json files and match by internal questionnaire_id field
    for path in sorted(OUTPUTS.glob("questionnaire_bank_*.json")):
        try:
            data = load_json(path)
            if data.get("questionnaire_id") == questionnaire_id:
                return data
        except Exception:
            continue

    # 3. Nothing found
    return None

def list_questionnaires():
    questionnaires = []
    seen_ids: set = set()
    for qfile in sorted(OUTPUTS.glob('questionnaire_*.json')):
        name = qfile.name
        # The `questionnaire_*.json` glob also matches artefacts that are NOT
        # questionnaire definitions and must never become dashboard cards:
        #   - questionnaire_answers_*.json : submitted answers (no
        #     questionnaire_type, so they rendered as a broken `control` card
        #     linking to /questionnaire/None).
        #   - *.merged-summary.json        : merge debug output.
        if name.startswith('questionnaire_answers_') or '.merged-summary.' in name:
            continue
        try:
            summary = questionnaire_summary(load_json(qfile))
        except Exception as exc:
            logger.warning('Skipping unreadable questionnaire %s: %s', qfile, exc)
            continue

        # Only show cards that can actually be opened, so a card never links to a
        # dead route (e.g. /questionnaire/None or a missing domain file).
        qtype = summary.get('type')
        if qtype == 'multi_framework':
            openable = bool(summary.get('questionnaire_id'))
        elif qtype == 'domain':
            did = str(summary.get('domain_id') or '').upper()
            openable = bool(did) and (OUTPUTS / f'questionnaire_domain_{did}.json').exists()
        else:  # control
            openable = bool(summary.get('control_id'))
        if not openable:
            logger.info('Hiding un-openable questionnaire card: %s', qfile.name)
            continue

        # De-duplicate by questionnaire_id so a given questionnaire shows once.
        qid = summary.get('questionnaire_id')
        if qid and qid in seen_ids:
            continue
        if qid:
            seen_ids.add(qid)
        questionnaires.append(summary)
    return questionnaires


def load_optional_json(path: Path, default):
    if not path.exists():
        return default
    return load_json(path)


def collect_part1_answers(request_obj) -> list[dict]:
    part1 = load_optional_json(PART1_QUESTIONS_PATH, {"questions": []})
    answers = []
    for row in part1.get("questions", []):
        qid = row.get("question_id")
        if not qid:
            continue
        response = request_obj.form.get(f"p1_{qid}", "")
        detail = request_obj.form.get(f"p1_detail_{qid}", "")
        answers.append({
            "question_id": qid,
            "number": row.get("number"),
            "cluster": row.get("cluster"),
            "sub_topic": row.get("sub_topic"),
            "question_text": row.get("question_text"),
            "answer_format": row.get("answer_format"),
            "response": response,
            "detail": detail,
        })
    return answers


@app.context_processor
def inject_static_part1():
    # Part 1 pre-assessment questions are now generated per-questionnaire by the LLM
    # (stored in q.part1_checklist). The static part1_questions.json is kept for
    # the /part1-questions reference page only.
    return {
        "static_part1": load_optional_json(PART1_QUESTIONS_PATH, {"questions": []})
    }


@app.route('/')
def index():
    catalog = load_framework_catalog()
    domain_count = sum(len(framework.get('domains', [])) for framework in catalog)
    return render_template(
        'index.html',
        questionnaires=list_questionnaires(),
        frameworks=catalog,
        domain_count=domain_count,
    )


@app.route('/part1-questions')
def show_part1_questions():
    data = load_optional_json(PART1_QUESTIONS_PATH, {"questions": []})
    return render_template('part1_questions.html', part1=data)


@app.route('/api/part1-questions')
def api_part1_questions():
    return jsonify(load_optional_json(PART1_QUESTIONS_PATH, {"questions": []}))


@app.route('/api/maturity-scoring-criteria')
def api_maturity_scoring_criteria():
    return jsonify(load_optional_json(MATURITY_SCORING_PATH, {"criteria": []}))


@app.route('/generate-domain', methods=['POST'])
def generate_domain():
    domain_id = (request.form.get('domain_id') or '').strip().upper()
    if not domain_id:
        flash('Choose a domain before generating questions', 'warning')
        return redirect(url_for('index'))

    framework = exported_nist_framework()
    vdb = VectorDBManager()
    domain = next((d for d in framework.get('domains', []) if d.get('domain_id') == domain_id), None)
    if not domain:
        raise ValueError(f'Domain not found in framework: {domain_id}')
    docs = vdb.search_framework(
        query=f"{framework.get('framework_name', '')} {domain.get('domain_name', domain_id)}",
        k=10,
        filter={
            'framework_id': framework.get('framework_id', 'nist-csf-2-0'),
            'domain_id': domain_id,
        },
    )
    questionnaire = build_domain_questionnaire_from_vdb(framework, domain, docs, question_count=10)
    outpath = OUTPUTS / f'questionnaire_domain_{domain_id}.json'
    save_json(questionnaire, outpath)
    save_questionnaire_xlsx(questionnaire, OUTPUTS)
    flash(f'Generated 10-question questionnaire for {domain_id}', 'success')
    return redirect(url_for('show_domain_questionnaire', domain_id=domain_id))


@app.route('/generate-questionnaire-bank', methods=['POST'])
def generate_questionnaire_bank():
    # Form sends "framework_key/domain_id" (slash); the exporter expects "framework_key:domain_id" (colon)
    selected_domain_keys = [
        v.strip().replace('/', ':', 1)
        for v in request.form.getlist('domains')
        if v.strip()
    ]

    if not selected_domain_keys:
        flash('Choose at least one framework domain before generating questions', 'warning')
        return redirect(url_for('index'))

    questionnaire = build_multi_framework_questionnaire(load_framework_catalog(), selected_domain_keys, question_count=10)
    outpath = questionnaire_file_path(questionnaire['questionnaire_id'])
    save_json(questionnaire, outpath)
    save_questionnaire_xlsx(questionnaire, OUTPUTS)
    flash(
        f'Generated {questionnaire["question_count"]} questions across {len(questionnaire["question_groups"])} selected domains',
        'success',
    )
    return redirect(url_for('show_questionnaire_bank', questionnaire_id=questionnaire['questionnaire_id']))
@app.route("/questionnaire-bank/<path:questionnaire_id>")   # ← path: handles long slugs with hyphens
def show_questionnaire_bank(questionnaire_id: str):
    data = load_questionnaire_bank(questionnaire_id)         # ← scans by JSON field if exact file not found
    if data is None:
        abort(404, ...)
    return render_template("questionnaire.html", q=data)

@app.route('/questionnaire/<control_id>', methods=['GET'])
def show_questionnaire(control_id):
    qfile = OUTPUTS / 'questionnaire_neo4j.json'
    if not qfile.exists():
        flash('Questionnaire not found; run generator first', 'warning')
        return redirect(url_for('index'))
    q = load_json(qfile)
    if q.get('control_id') != control_id:
        flash('Control not found in questionnaire', 'warning')
        return redirect(url_for('index'))
    return render_template('questionnaire.html', q=q)

@app.route('/domain/<domain_id>', methods=['GET'])
def show_domain_questionnaire(domain_id):
    domain_id = domain_id.upper()
    qfile = OUTPUTS / f'questionnaire_domain_{domain_id}.json'
    if not qfile.exists():
        flash('Domain questionnaire not found; generate it first', 'warning')
        return redirect(url_for('index'))
    q = load_json(qfile)
    return render_template('questionnaire.html', q=q)


_QTYPE_NORM = {
    'YES_NO': 'yes_no',
    'SCALE_1_5': 'scale_1_5',
    'MULTI_CHOICE': 'multi_choice',
    'FREE_TEXT': 'free_text',
    'EVIDENCE_UPLOAD': 'evidence_upload',
}


def _get_form_questions(q: dict) -> list:
    """Return the questions that were actually rendered in the submitted form.

    For multi-framework questionnaires the form renders sub-topic questions
    (question_groups → sub_topics → questions), NOT the flat atomic questions.
    For everything else the flat questions list is used.
    """
    if q.get('questionnaire_type') == 'multi_framework':
        result, seen = [], set()
        for group in q.get('question_groups', []):
            domain_id = group.get('domain_id', '')
            sub_topics = group.get('sub_topics', []) or []

            # Count the sub-topic questions this group renders.
            group_added = 0
            for section in sub_topics:
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
                        'choices': sq.get('choices', []),
                    })
                    group_added += 1

            # Fallback: when a group has no sub-topic questions, the form renders
            # the group's own (merged/flat) questions — collect those instead so
            # submitted answers match what was shown. (Mirrors questionnaire.html.)
            if group_added == 0:
                for gq in group.get('questions', []) or []:
                    qid = gq.get('question_id')
                    if not qid or qid in seen:
                        continue
                    seen.add(qid)
                    # Preserve full dict (answer_formats/source_questions) so
                    # collect_question_answer handles merged + single formats.
                    item = dict(gq)
                    item.setdefault('domain_id', domain_id)
                    result.append(item)
        return result
    return q.get('questions', [])


@app.route('/submit-questionnaire/<questionnaire_id>', methods=['POST'])
def submit_questionnaire(questionnaire_id):
    qfile = questionnaire_file_path(questionnaire_id)
    q = load_json(qfile)
    answers = {
        'questionnaire_id': q.get('questionnaire_id') or questionnaire_id,
        'framework_names': q.get('framework_names', []),
        'selected_domain_keys': q.get('selected_domain_keys', []),
        'control_id': q.get('control_id'),
        'domain_id': q.get('domain_id'),
        'domain_name': q.get('domain_name'),
        'domain_description': q.get('domain_description'),
        'control_statement': q.get('control_statement'),
        'part1_answers': collect_part1_answers(request),
        'answers': [],
        'answered_at': None,
        'auditor': request.form.get('auditor', 'web-auditor'),
    }

    for qq in _get_form_questions(q):
        answers['answers'].append(collect_question_answer(qq, request, EVIDENCE, questionnaire_id))

    outpath = OUTPUTS / f"questionnaire_answers_{answers['questionnaire_id']}.json"
    save_json(answers, outpath)
    save_questionnaire_xlsx(q, OUTPUTS)
    score_answers(qfile, outpath)
    flash('Questions submitted and report generated', 'success')
    return redirect(url_for('questionnaire_report', questionnaire_id=answers['questionnaire_id']))


def extract_text_from_pdf(path: Path) -> str:
    try:
        with open(path, 'rb') as fh:
            reader = PdfReader(fh)
            texts = []
            for p in reader.pages:
                texts.append(p.extract_text() or '')
            return '\n'.join(texts)
    except Exception as e:
        logger.warning('PDF extraction failed: %s', e)
        return ''


def extract_preview_text(file_path: Path) -> str:
    """Extract a short text preview from supported evidence files."""
    suffix = file_path.suffix.lower()
    try:
        if suffix == '.pdf':
            return extract_text_from_pdf(file_path)
        if suffix in {'.txt', '.md', '.csv', '.json', '.xml', '.log'}:
            return file_path.read_text(encoding='utf-8', errors='ignore')
        if suffix == '.docx':
            doc = DocxDocument(str(file_path))
            return '\n'.join(p.text for p in doc.paragraphs)
        if suffix in {'.xlsx', '.xls'}:
            import openpyxl
            wb = openpyxl.load_workbook(str(file_path), read_only=True, data_only=True)
            rows = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        rows.append('\t'.join(cells))
                    if len(rows) >= 200:
                        break
                if len(rows) >= 200:
                    break
            wb.close()
            return '\n'.join(rows)
    except Exception as exc:
        logger.warning('Preview extraction failed for %s: %s', file_path, exc)
    return ''


def save_evidence_file(upload, dest: Path) -> tuple[str, str]:
    """Save evidence upload and return (saved_path, extracted_text)."""
    dest.mkdir(parents=True, exist_ok=True)
    fname = werkzeug.utils.secure_filename(upload.filename)
    path = dest / fname
    upload.save(path)
    extracted = extract_preview_text(path)
    if extracted:
        (dest / f'{fname}.txt').write_text(extracted, encoding='utf-8')
        # Chunk and store evidence into VectorDB evidence collection (best-effort)
        try:
            vdb = VectorDBManager()
            chunker = HierarchicalChunker()
            chunks = chunker.chunk_evidence(extracted, source_name=fname)
            vdb.store_evidence_docs(chunks)
        except Exception as exc:
            logger.warning('Failed to store evidence chunks in VectorDB: %s', exc)
    return str(path), extracted


def collect_question_answer(question: dict, request_obj, evidence_root: Path, scope_id: str) -> dict:
    """Collect a single answer, including merged multi-format question payloads."""
    qid = question.get('question_id')
    answer_formats = question.get('answer_formats') or [question.get('question_type', 'free_text')]
    is_merged = question.get('question_type') == 'merged' or len(answer_formats) > 1

    if not is_merged:
        qtype = question.get('question_type')
        field = f'response_{qid}'
        note_field = f'note_{qid}'
        resp = request_obj.form.get(field, '')
        note = request_obj.form.get(note_field, '')

        if qtype == 'evidence_upload':
            f = request_obj.files.get(field)
            if f and f.filename:
                saved_path, extracted_text = save_evidence_file(f, evidence_root / scope_id)
                return {
                    'question_id': qid,
                    'question_text': question.get('question_text'),
                    'question_type': qtype,
                    'response': saved_path,
                    'note': note,
                    'evidence_preview': extracted_text[:1000],
                }
            return {
                'question_id': qid,
                'question_text': question.get('question_text'),
                'question_type': qtype,
                'response': '',
                'note': note,
            }

        return {
            'question_id': qid,
            'question_text': question.get('question_text'),
            'question_type': qtype,
            'response': resp,
            'note': note,
        }

    merged_answer = {
        'question_id': qid,
        'question_text': question.get('question_text'),
        'question_type': 'merged',
        'answer_formats': answer_formats,
        'response': {},
        'note': {},
        'source_questions': question.get('source_questions', []),
    }

    for fmt in answer_formats:
        field = f'response_{qid}__{fmt}'
        note_field = f'note_{qid}__{fmt}'
        fmt_note = request_obj.form.get(note_field, '')

        if fmt == 'evidence_upload':
            f = request_obj.files.get(field)
            if f and f.filename:
                saved_path, extracted_text = save_evidence_file(f, evidence_root / scope_id)
                merged_answer['response'][fmt] = saved_path
                merged_answer['note'][fmt] = fmt_note
                merged_answer.setdefault('evidence_preview', {})[fmt] = extracted_text[:1000]
            else:
                merged_answer['response'][fmt] = ''
                merged_answer['note'][fmt] = fmt_note
        else:
            merged_answer['response'][fmt] = request_obj.form.get(field, '')
            merged_answer['note'][fmt] = fmt_note

    return merged_answer


@app.route('/preview-evidence', methods=['POST'])
def preview_evidence():
    """Preview text for a single uploaded evidence file before form submit."""
    upload = request.files.get('file')
    if not upload or not upload.filename:
        return jsonify({'ok': False, 'error': 'No file uploaded'}), 400

    suffix = Path(upload.filename).suffix.lower()
    temp_dir = EVIDENCE / '_previews'
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / werkzeug.utils.secure_filename(upload.filename)
    upload.save(temp_path)
    preview_text = extract_preview_text(temp_path)
    try:
        temp_path.unlink(missing_ok=True)
    except Exception:
        pass

    return jsonify({
        'ok': True,
        'filename': upload.filename,
        'file_type': suffix.lstrip('.'),
        'preview_text': preview_text[:2000],
        'has_preview': bool(preview_text),
    })


@app.route('/submit/<control_id>', methods=['POST'])
def submit(control_id):
    qfile = OUTPUTS / 'questionnaire_neo4j.json'
    q = load_json(qfile)
    answers = {
        'control_id': control_id,
        'control_statement': q.get('control_statement'),
        'part1_answers': collect_part1_answers(request),
        'answers': [],
        'answered_at': None,
        'auditor': request.form.get('auditor', 'web-auditor'),
    }

    # handle questions
    for qq in q.get('questions', []):
        qid = qq.get('question_id')
        qtype = qq.get('question_type')
        field = f'response_{qid}'
        resp = request.form.get(field, '')

        # file upload for evidence
        if qtype == 'evidence_upload':
            f = request.files.get(field)
            if f and f.filename:
                dest = EVIDENCE / control_id
                saved_path, extracted_text = save_evidence_file(f, dest)
                answers['answers'].append({
                    'question_id': qid,
                    'question_text': qq.get('question_text'),
                    'question_type': qtype,
                    'response': saved_path,
                    'note': request.form.get(f'note_{qid}',''),
                    'evidence_preview': extracted_text[:1000],
                })
            else:
                answers['answers'].append({'question_id': qid, 'question_text': qq.get('question_text'), 'question_type': qtype, 'response': '', 'note': request.form.get(f'note_{qid}','')})
        else:
            answers['answers'].append({'question_id': qid, 'question_text': qq.get('question_text'), 'question_type': qtype, 'response': resp, 'note': request.form.get(f'note_{qid}','')})

    outpath = OUTPUTS / f'questionnaire_answers_{control_id}.json'
    save_json(answers, outpath)

    # score and generate report
    scored = score_answers(qfile, outpath)

    flash('Answers submitted and report generated', 'success')
    return redirect(url_for('report', control_id=control_id))


@app.route('/submit-domain/<domain_id>', methods=['POST'])
def submit_domain(domain_id):
    domain_id = domain_id.upper()
    qfile = OUTPUTS / f'questionnaire_domain_{domain_id}.json'
    q = load_json(qfile)
    answers = {
        'questionnaire_id': q.get('questionnaire_id') or f'domain-{domain_id}',
        'control_id': domain_id,
        'domain_id': domain_id,
        'domain_name': q.get('domain_name'),
        'domain_description': q.get('domain_description'),
        'control_statement': q.get('domain_description'),
        'part1_answers': collect_part1_answers(request),
        'answers': [],
        'answered_at': None,
        'auditor': request.form.get('auditor', 'web-auditor'),
    }

    for qq in q.get('questions', []):
        qid = qq.get('question_id')
        qtype = qq.get('question_type')
        field = f'response_{qid}'
        resp = request.form.get(field, '')

        if qtype == 'evidence_upload':
            f = request.files.get(field)
            if f and f.filename:
                dest = EVIDENCE / domain_id
                saved_path, extracted_text = save_evidence_file(f, dest)
                answers['answers'].append({
                    'question_id': qid,
                    'question_text': qq.get('question_text'),
                    'question_type': qtype,
                    'response': saved_path,
                    'note': request.form.get(f'note_{qid}',''),
                    'evidence_preview': extracted_text[:1000],
                })
            else:
                answers['answers'].append({'question_id': qid, 'question_text': qq.get('question_text'), 'question_type': qtype, 'response': '', 'note': request.form.get(f'note_{qid}','')})
        else:
            answers['answers'].append({'question_id': qid, 'question_text': qq.get('question_text'), 'question_type': qtype, 'response': resp, 'note': request.form.get(f'note_{qid}','')})

    outpath = OUTPUTS / f"questionnaire_answers_{answers['questionnaire_id']}.json"
    save_json(answers, outpath)
    scored = score_answers(qfile, outpath)
    flash('Domain answers submitted and report generated', 'success')
    return redirect(url_for('domain_report', domain_id=domain_id))


@app.route('/report/<control_id>')
def report(control_id):
    rfile = OUTPUTS / f'questionnaire_report_{control_id}.html'
    if not rfile.exists():
        flash('Report not found — run questionnaire and submit answers', 'warning')
        return redirect(url_for('index'))
    return send_from_directory(rfile.parent, rfile.name)


@app.route('/report/domain/<domain_id>')
def domain_report(domain_id):
    domain_id = domain_id.upper()
    report_id = f'domain-{domain_id}'
    rfile = OUTPUTS / f'questionnaire_report_{report_id}.html'
    if not rfile.exists():
        flash('Domain report not found — generate and submit answers first', 'warning')
        return redirect(url_for('index'))
    return send_from_directory(rfile.parent, rfile.name)


@app.route('/report/questionnaire/<questionnaire_id>')
def questionnaire_report(questionnaire_id):
    rfile = OUTPUTS / f'questionnaire_report_{questionnaire_id}.html'
    if not rfile.exists():
        flash('Report not found — generate and submit answers first', 'warning')
        return redirect(url_for('index'))
    return send_from_directory(rfile.parent, rfile.name)


@app.route('/upload-evidence-bulk', methods=['POST'])
def upload_evidence_bulk():
    """Accept multiple XLSX / DOCX evidence files, parse & store them, return JSON summary."""
    ALLOWED = {'.xlsx', '.xls', '.docx'}
    scope_id = request.form.get('scope_id', 'bulk')
    dest = EVIDENCE / werkzeug.utils.secure_filename(scope_id)
    dest.mkdir(parents=True, exist_ok=True)

    results = []
    files = request.files.getlist('evidence_files')
    if not files or all(not f.filename for f in files):
        return jsonify({'ok': False, 'error': 'No files uploaded'}), 400

    for upload in files:
        if not upload or not upload.filename:
            continue
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in ALLOWED:
            results.append({
                'filename': upload.filename,
                'ok': False,
                'error': f'Unsupported type {suffix}. Only XLSX and DOCX are accepted.',
            })
            continue
        try:
            saved_path, extracted = save_evidence_file(upload, dest)
            from backend.core.evaluation_metrics import score_evidence_quality
            ev_score = score_evidence_quality(saved_path, extracted)
            results.append({
                'filename': upload.filename,
                'ok': True,
                'saved_path': saved_path,
                'preview': extracted[:500],
                'evidence_score': ev_score,
                'evidence_grade': (
                    'operational' if ev_score >= 100 else
                    'policy_implemented' if ev_score >= 75 else
                    'policy_only' if ev_score >= 40 else
                    'filename_only' if ev_score >= 20 else
                    'missing'
                ),
            })
        except Exception as exc:
            logger.warning('Bulk upload failed for %s: %s', upload.filename, exc)
            results.append({'filename': upload.filename, 'ok': False, 'error': str(exc)})

    return jsonify({'ok': True, 'files': results, 'count': len(results)})


# ── Evidence-based answer pre-fill ────────────────────────────────────────────

PREFILL_SYSTEM_PROMPT = """\
You are a compliance analyst. You are given EVIDENCE documents collected from an
organisation and a list of assessment questions.

Answer EACH question STRICTLY and ONLY using facts stated in the provided
evidence. This is the absolute rule:
- Do NOT use outside knowledge, assumptions, or industry best practice.
- If the evidence does not contain enough information to answer a question, set
  "supported" to false and leave "answer" empty.
- Name the specific evidence file your answer is based on in "source".

Answer format by question_type:
- yes_no        : "Yes", "No", or "Partial"
- scale_1_5     : an integer 1-5 (1 = ad-hoc/initial, 5 = optimised)
- multi_choice  : EXACTLY one of the provided choices, copied verbatim
- maturity_rating: one of "Initial","Developing","Defined","Managed","Optimizing"
- free_text     : a concise answer (<= 80 words) stating only what the evidence shows

Be conservative: prefer "supported": false over guessing.

Return ONLY valid JSON (no markdown, no commentary):
{
  "answers": [
    {
      "question_id": "<id>",
      "answer": <type-appropriate value, or "" if unsupported>,
      "supported": true,
      "rationale": "<one sentence grounded in the evidence>",
      "source": "<evidence filename the answer is based on>"
    }
  ]
}
"""


def _build_evidence_corpus(sections, max_chars: int = 60000) -> str:
    """Join (filename, text) evidence sections into a single capped corpus."""
    parts: list[str] = []
    used = 0
    for name, text in sections:
        if used >= max_chars:
            break
        header = f"\n### EVIDENCE FILE: {name}\n"
        budget = max_chars - used
        body = text if len(text) <= budget else (text[:budget] + "\n…[truncated]")
        chunk = header + body
        parts.append(chunk)
        used += len(chunk)
    return "".join(parts)


def _select_evidence_for_batch(sections, batch_questions, total_cap: int = 16000, per_file_cap: int = 4000):
    """Pick the evidence a question batch should see, ranked by relevance.

    The previous design fed a single 12k-char global corpus (only the
    alphabetically-first files) to every batch, so later domains (APP, TPRM) got
    no evidence at all. Sending the FULL corpus to every batch instead fixed the
    visibility but blew past the LLM token-rate limit. So now each batch receives
    only the files most relevant to ITS questions, capped to a modest budget:
      * keeps every per-call payload small (avoids 429 rate limits), and
      * still guarantees, e.g., the APP batch pulls the application files and the
        TPRM batch pulls the vendor files — never just the first files on disk.
    """
    terms: dict = {}
    for q in batch_questions:
        blob = ' '.join([
            str(q.get('question_text') or q.get('text') or ''),
            str(q.get('control_id') or ''),
            str(q.get('domain_id') or ''),
        ]).lower()
        for w in re.findall(r'[a-z][a-z0-9]{3,}', blob):
            terms[w] = terms.get(w, 0) + 1

    def _score(item):
        name, text = item
        hay = (name + ' ' + text[:4000]).lower()
        return sum(hay.count(w) * c for w, c in terms.items())

    ranked = sorted(sections, key=_score, reverse=True)
    chosen, used = [], 0
    for name, text in ranked:
        if used >= total_cap:
            break
        body = text[:per_file_cap]
        chosen.append((name, body))
        used += len(body)
    return chosen


def _prefill_answers_with_llm(questions, evidence_corpus, api_key, model, max_tokens=3000):
    """Ask the LLM to answer a batch of questions strictly from the evidence."""
    payload_qs = [
        {
            "question_id": qq.get("question_id"),
            "question_type": (qq.get("question_type") or "free_text").lower(),
            "question_text": qq.get("question_text") or qq.get("text", ""),
            "choices": qq.get("choices") or [],
        }
        for qq in questions
    ]
    import json as _json
    user_prompt = (
        "EVIDENCE DOCUMENTS:\n"
        f"{evidence_corpus}\n\n"
        "QUESTIONS (answer each strictly from the evidence above):\n"
        f"{_json.dumps(payload_qs, indent=2)}\n\n"
        "Return the JSON object now."
    )
    # Free-tier rate limits are PER MODEL, and 70b's are very low. Try the
    # high-throughput 8b model first, then fall back to the configured model and
    # 70b — so one rate-limited model can't sink the whole pre-fill.
    model_chain: list = []
    for m in ["openai/gpt-oss-20b", model, "openai/gpt-oss-120b"]:
        if m and m not in model_chain:
            model_chain.append(m)
    client = LLMClient(api_key, model_chain, temperature=0.0)
    raw = client.invoke({
        "system_prompt": PREFILL_SYSTEM_PROMPT,
        "user_prompt": user_prompt,
        "max_tokens": max_tokens,
    })
    parsed = parse_llm_json(raw)
    if isinstance(parsed, dict):
        return parsed.get("answers", []) or []
    if isinstance(parsed, list):
        return parsed
    return []


def _map_prefill_answer(meta: dict, a: dict) -> dict | None:
    """Map a raw LLM answer to a form-ready {response, note, supported} dict."""
    qtype = (meta.get("question_type") or "free_text").lower()
    supported = bool(a.get("supported"))
    raw = a.get("answer")
    raw_str = "" if raw is None else str(raw).strip()

    response = ""
    if supported and raw_str:
        if qtype == "yes_no":
            t = raw_str.lower()
            response = "yes" if t.startswith("y") else "partial" if t.startswith("p") else "no" if t.startswith("n") else ""
        elif qtype == "scale_1_5":
            m = re.search(r"[1-5]", raw_str)
            response = m.group(0) if m else ""
        elif qtype == "multi_choice":
            choices = meta.get("choices") or []
            match = next((c for c in choices if c.strip().lower() == raw_str.lower()), None)
            if not match:
                match = next((c for c in choices if raw_str.lower() in c.strip().lower() or c.strip().lower() in raw_str.lower()), None)
            response = match or raw_str
        elif qtype == "maturity_rating":
            ladder = ["Initial", "Developing", "Defined", "Managed", "Optimizing"]
            response = next((m for m in ladder if m.lower() == raw_str.lower()), raw_str.capitalize())
        else:  # free_text and anything else
            response = raw_str

    rationale = str(a.get("rationale") or "").strip()
    source = str(a.get("source") or "").strip()
    if supported and response:
        note = "[AI pre-filled] " + (rationale or "Derived from submitted evidence.")
        if source:
            note += f" (Source: {source})"
    else:
        note = "[AI pre-fill] Insufficient evidence to answer this question."
    return {"response": response, "note": note, "supported": supported and bool(response)}


@app.route('/prefill-answers/<path:questionnaire_id>', methods=['POST'])
def prefill_answers(questionnaire_id):
    """Generate draft answers for a questionnaire STRICTLY from its uploaded evidence.

    Returns JSON only — the answers are filled into the form client-side for the
    auditor to review; nothing is submitted until they click Save & Submit.
    """
    q = load_questionnaire_bank(questionnaire_id)
    if q is None:
        return jsonify({'ok': False, 'error': 'Questionnaire not found'}), 404

    # 1. Gather extracted text from evidence uploaded for this questionnaire.
    ev_dir = EVIDENCE / werkzeug.utils.secure_filename(questionnaire_id)
    sections = []
    if ev_dir.exists():
        for txt in sorted(ev_dir.glob('*.txt')):
            try:
                content = txt.read_text(encoding='utf-8', errors='ignore').strip()
            except Exception:
                continue
            if content:
                sections.append((txt.name[:-4], content))  # drop the .txt suffix

    if not sections:
        return jsonify({
            'ok': True,
            'answers': {},
            'summary': {
                'filled': 0, 'total': 0, 'evidence_files': [],
                'message': 'No evidence found. Upload evidence files above, then pre-fill.',
            },
        })

    # 2. Collect answerable questions (skip evidence_upload — cannot pre-fill a file).
    questions = [
        qq for qq in _get_form_questions(q)
        if (qq.get('question_type') or '').lower() != 'evidence_upload'
    ]
    if not questions:
        return jsonify({'ok': True, 'answers': {},
                        'summary': {'filled': 0, 'total': 0,
                                    'evidence_files': [s[0] for s in sections]}})

    api_key = os.getenv('GROQ_API_KEY', os.getenv('GROQ_KEY', '')).strip()
    if not api_key:
        return jsonify({'ok': False, 'error': 'LLM not configured (GROQ_API_KEY missing).'}), 503
    model = os.getenv('GROQ_MODEL', 'openai/gpt-oss-120b').strip() or 'openai/gpt-oss-120b'

    # 3. Generate answers, strictly grounded in the evidence.
    #    The whole evidence package is small (a few thousand tokens), so the
    #    reliable path is ONE call carrying every question + the full corpus —
    #    every question sees every file, and it is a single rate-limit hit.
    #    Only leftovers (or very large questionnaires) fall back to spaced,
    #    evidence-scoped domain batches.
    import time
    from collections import OrderedDict

    answers: dict = {}

    def _apply(results, batch):
        by_id = {qq['question_id']: qq for qq in batch}
        for a in (results or []):
            meta = by_id.get(a.get('question_id'))
            if not meta:
                continue
            mapped = _map_prefill_answer(meta, a)
            if mapped:
                answers[meta['question_id']] = mapped

    llm_error: dict = {'failed': False}

    def _call(batch_qs, corpus, max_toks):
        try:
            return _prefill_answers_with_llm(batch_qs, corpus, api_key, model, max_tokens=max_toks)
        except Exception as exc:
            llm_error['failed'] = True
            logger.warning('Prefill LLM call failed: %s', exc)
            return []

    # Compact each evidence file (the extracted tables are most informative at the
    # top — header row + first rows), so EVERY file fits in a request small enough
    # for the most-limited free-tier model (openai/gpt-oss-20b ≈ 6k tokens/req,
    # but a large daily budget). This keeps all domains' evidence visible AND keeps
    # prompt + completion under the per-request ceiling that causes 413, while the
    # 8b model's big daily quota avoids the 70b daily-quota 429s.
    PER_FILE_CAP = 380
    compact = [(n, t[:PER_FILE_CAP]) for n, t in sections]

    if len(questions) <= 30:
        # One small call covering every question — all files visible, single hit.
        # Budget ~130 completion tokens per answer (id + value + rationale +
        # source) so the JSON isn't truncated mid-answer; parse_llm_json can
        # salvage a truncated tail, but fitting the whole response is better.
        corpus = _build_evidence_corpus(compact, max_chars=10500)
        max_toks = min(8000, max(1900, len(questions) * 130))
        _apply(_call(questions, corpus, max_toks), questions)
    else:
        # Large questionnaire: per-domain batches with scoped evidence, spaced out
        # to respect the per-minute token budget.
        groups: "OrderedDict[str, list]" = OrderedDict()
        for qq in questions:
            groups.setdefault(qq.get('domain_id') or '_', []).append(qq)
        BATCH = 8
        batches = [qs[j:j + BATCH] for qs in groups.values() for j in range(0, len(qs), BATCH)]
        for bi, batch in enumerate(batches):
            if bi > 0:
                time.sleep(20)  # stay under the per-minute token budget
            corpus = _build_evidence_corpus(
                _select_evidence_for_batch(compact, batch, total_cap=7000, per_file_cap=PER_FILE_CAP),
                max_chars=7000,
            )
            # ~130 completion tokens per answer so each batch's JSON closes
            # cleanly (BATCH=8 → ~1040), with headroom.
            _apply(_call(batch, corpus, max(1800, len(batch) * 150)), batch)

    # No answers AND the LLM calls errored → tell the user the real reason
    # (rate-limited / unavailable) rather than the misleading "no evidence".
    if not answers and llm_error['failed']:
        return jsonify({
            'ok': False,
            'error': 'The AI service is busy or rate-limited right now. Please wait 30-60 seconds and click "Pre-fill from Evidence" again.',
        }), 503

    filled = sum(1 for v in answers.values() if v.get('response'))
    return jsonify({
        'ok': True,
        'answers': answers,
        'summary': {
            'filled': filled,
            'total': len(questions),
            'evidence_files': [s[0] for s in sections],
        },
    })


_ALLOWED_FRAMEWORK_EXTENSIONS = {'.pdf', '.json', '.xlsx', '.xls', '.csv', '.xml'}


@app.route('/ingest-framework', methods=['POST'])
def ingest_framework():
    """Accept a compliance framework document, run the dynamic ingestion pipeline, and redirect."""
    upload  = request.files.get('framework_file')
    name    = (request.form.get('framework_name') or '').strip()
    version = (request.form.get('framework_version') or '').strip()

    if not upload or not upload.filename:
        flash('No file selected.', 'error')
        return redirect(url_for('index'))
    if not name:
        flash('Framework name is required.', 'error')
        return redirect(url_for('index'))

    suffix = Path(upload.filename).suffix.lower()
    if suffix not in _ALLOWED_FRAMEWORK_EXTENSIONS:
        flash(f'Unsupported file type "{suffix}". Upload PDF, JSON, XLSX, CSV, or XML.', 'error')
        return redirect(url_for('index'))

    UPLOADS.mkdir(parents=True, exist_ok=True)
    fname = werkzeug.utils.secure_filename(upload.filename)
    dest  = UPLOADS / fname
    upload.save(dest)

    try:
        from backend.ingestion.dynamic_pipeline import ingest_file
        result = ingest_file(
            file_path         = dest,
            framework_name    = name,
            framework_version = version,
        )
        overwrite_note = ' (previous version overwritten)' if result.get('overwritten') else ''
        flash(
            f'"{name}" ingested — {result["n_controls"]} controls across {result["n_domains"]} domains '
            f'in collection "{result["collection_name"]}"{overwrite_note}.',
            'success',
        )
    except FileNotFoundError as exc:
        flash(f'File not found after upload: {exc}', 'error')
    except ValueError as exc:
        flash(f'Could not extract text from file: {exc}', 'error')
    except RuntimeError as exc:
        flash(f'Ingestion configuration error: {exc}', 'error')
    except Exception as exc:
        logger.error('Framework ingestion failed: %s', exc, exc_info=True)
        flash(f'Ingestion failed: {exc}', 'error')
    finally:
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass

    return redirect(url_for('index'))


if __name__ == '__main__':
    # Bind to localhost and disable the debugger reloader to avoid
    # spurious restarts caused by system file changes on Windows.
    app.run(host='127.0.0.1', port=int(os.getenv('PORT', 5000)), debug=False, use_reloader=False)
