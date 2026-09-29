// Mirror of the FastAPI response models (app/schemas/models.py).

export interface Framework {
  id: number;
  code: string;
  name: string;
  version: string;
  description: string | null;
  source_format: string | null;
  source_file: string | null;
  external_uuid: string | null;
  ingested_at: string | null;
  created_at: string;
  node_count: number;
  control_count: number;
  question_count: number;
}

export interface TreeNode {
  id: number;
  parent_id: number | null;
  node_type: string;
  code: string;
  name: string;
  control_count: number;
  descendant_control_count: number;
  children: TreeNode[];
}

export interface Question {
  id?: number | null;
  question_code?: string | null;
  text: string;
  question_type: string;
  choices?: unknown;
  help_text?: string | null;
  weight?: number | null;
  is_synthesized: boolean;
  sort_order: number;
}

export interface ControlListItem {
  id: number;
  control_id: string;
  name: string | null;
  statement: string;
  node_id: number;
  node_code: string;
  requires_evidence: boolean;
  question_count: number;
  sort_order: number;
}

export interface ControlDetail {
  id: number;
  framework_id: number;
  node_id: number;
  node_code: string;
  node_path: string[];
  control_id: string;
  name: string | null;
  statement: string;
  requires_evidence: boolean;
  sort_order: number;
  attributes: Record<string, unknown> | null;
  questions: Question[];
  evidence_types: string[];
  row_hash: string;
}

export interface ControlUpdate {
  name?: string | null;
  statement?: string;
  requires_evidence?: boolean;
  attributes?: Record<string, unknown> | null;
  questions?: Question[];
  evidence_types?: string[];
  row_hash?: string;
}

export interface SearchHit {
  control_pk: number;
  framework_id: number;
  framework_code: string;
  control_id: string;
  name: string | null;
  statement: string;
  node_code: string;
  match_field: string;
}

export interface ValidationIssue {
  severity: "error" | "warning";
  rule: string;
  message: string;
  entity_type: string;
  entity_id: number | null;
  entity_ref: string | null;
}

export interface ValidationReport {
  framework_id: number;
  ok: boolean;
  error_count: number;
  warning_count: number;
  issues: ValidationIssue[];
}

export interface DashboardStats {
  frameworks: number;
  nodes: number;
  controls: number;
  questions: number;
  assessments: number;
  synthesized_questions: number;
  controls_without_questions: number;
  validation_error_frameworks: number;
  frameworks_detail: Framework[];
  recent_activity: Array<{
    id: number;
    entity_type: string;
    entity_id: number;
    action: string;
    created_at: string;
  }>;
}

export interface HistoryEntry {
  id: number;
  action: string;
  changes: unknown;
  user_id: number | null;
  created_at: string;
}
