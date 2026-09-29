import { useEffect, useState } from "react";
import { useForm, useFieldArray } from "react-hook-form";
import {
  Save,
  Copy,
  Trash2,
  History as HistoryIcon,
  Plus,
  X,
  RotateCcw,
  ShieldCheck,
  FileText,
} from "lucide-react";
import {
  useControl,
  useControlHistory,
  useDeleteControl,
  useDuplicateControl,
  useUpdateControl,
} from "@/lib/queries";
import { useUi } from "@/stores/ui";
import { ApiError } from "@/lib/api";
import { Badge, Button, EmptyState, Spinner, useToasts } from "@/components/ui";
import { cn, questionTypeLabel, relativeTime } from "@/lib/utils";
import type { Question } from "@/lib/types";

interface FormValues {
  name: string;
  statement: string;
  requires_evidence: boolean;
  questions: Question[];
  evidence_types: string[];
}

const QUESTION_TYPES = ["YES_NO", "FREE_TEXT", "MULTI_CHOICE", "MATURITY_SCALE", "NUMERIC"];

export function DetailPanel() {
  const controlPk = useUi((s) => s.controlPk);
  const frameworkId = useUi((s) => s.frameworkId);
  const [tab, setTab] = useState<"editor" | "history">("editor");
  const { data: control, isLoading } = useControl(controlPk);
  const update = useUpdateControl(frameworkId);
  const duplicate = useDuplicateControl(frameworkId);
  const remove = useDeleteControl(frameworkId);
  const push = useToasts((s) => s.push);
  const selectControl = useUi((s) => s.selectControl);

  const form = useForm<FormValues>({
    defaultValues: { name: "", statement: "", requires_evidence: false, questions: [], evidence_types: [] },
  });
  const { register, control: formControl, handleSubmit, reset, formState } = form;
  const questions = useFieldArray({ control: formControl, name: "questions" });

  // Reset the form whenever a different control loads.
  useEffect(() => {
    if (control) {
      reset({
        name: control.name ?? "",
        statement: control.statement,
        requires_evidence: control.requires_evidence,
        questions: control.questions,
        evidence_types: control.evidence_types,
      });
      setTab("editor");
    }
  }, [control, reset]);

  if (controlPk == null)
    return (
      <div className="h-full border-l border-border-subtle bg-bg-raised">
        <EmptyState
          icon={<FileText className="h-8 w-8" />}
          title="No control selected"
          hint="Pick a control from the tree or grid to view and edit it."
        />
      </div>
    );

  if (isLoading || !control)
    return (
      <div className="flex h-full items-center justify-center border-l border-border-subtle bg-bg-raised">
        <Spinner />
      </div>
    );

  const onSave = handleSubmit((values) => {
    update.mutate(
      {
        pk: control.id,
        patch: {
          name: values.name || null,
          statement: values.statement,
          requires_evidence: values.requires_evidence,
          questions: values.questions,
          evidence_types: values.evidence_types.filter(Boolean),
          row_hash: control.row_hash,
        },
      },
      {
        onSuccess: () => push("Control saved", "ok"),
        onError: (err) => {
          if (err instanceof ApiError && err.status === 409)
            push("Conflict: reloaded latest version", "danger");
          else push(err instanceof Error ? err.message : "Save failed", "danger");
        },
      },
    );
  });

  return (
    <div className="flex h-full flex-col border-l border-border-subtle bg-bg-raised">
      {/* Header */}
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border-subtle px-4">
        <span className="font-mono text-sm font-semibold text-fg">{control.control_id}</span>
        <Badge>{control.node_path.join(" › ")}</Badge>
        <div className="ml-auto flex items-center gap-1">
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              duplicate.mutate(control.id, {
                onSuccess: (c) => {
                  push(`Duplicated as ${c.control_id}`, "ok");
                  selectControl(c.id);
                },
              })
            }
          >
            <Copy className="h-3.5 w-3.5" /> Duplicate
          </Button>
          <Button
            size="sm"
            variant="danger"
            onClick={() => {
              if (confirm(`Delete control ${control.control_id}? This cannot be undone.`))
                remove.mutate(control.id, {
                  onSuccess: () => {
                    push("Control deleted", "ok");
                    selectControl(null);
                  },
                });
            }}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex shrink-0 gap-1 border-b border-border-subtle px-3">
        {(["editor", "history"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "flex items-center gap-1.5 border-b-2 px-2 py-2 text-xs capitalize transition-colors",
              tab === t
                ? "border-accent text-fg"
                : "border-transparent text-fg-faint hover:text-fg-muted",
            )}
          >
            {t === "history" && <HistoryIcon className="h-3.5 w-3.5" />}
            {t}
          </button>
        ))}
      </div>

      {tab === "editor" ? (
        <>
          <div className="flex-1 space-y-5 overflow-y-auto p-4">
            {/* Core */}
            <Field label="Control name">
              <input
                {...register("name")}
                placeholder="(no name)"
                className="input"
              />
            </Field>

            <Field label="Statement">
              <textarea {...register("statement", { required: true })} rows={4} className="input resize-y" />
            </Field>

            <label className="flex items-center gap-2 text-sm text-fg-muted">
              <input type="checkbox" {...register("requires_evidence")} className="accent-accent" />
              <ShieldCheck className="h-3.5 w-3.5" /> Requires evidence
            </label>

            {/* Questions */}
            <div>
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs font-medium uppercase tracking-wide text-fg-faint">
                  Questions ({questions.fields.length})
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  type="button"
                  onClick={() =>
                    questions.append({
                      text: "",
                      question_type: "YES_NO",
                      is_synthesized: false,
                      sort_order: questions.fields.length,
                    })
                  }
                >
                  <Plus className="h-3.5 w-3.5" /> Add
                </Button>
              </div>
              <div className="space-y-2">
                {questions.fields.map((f, i) => (
                  <div key={f.id} className="rounded-md border border-border-subtle bg-bg-base p-2.5">
                    <div className="mb-1.5 flex items-center gap-2">
                      <select
                        {...register(`questions.${i}.question_type` as const)}
                        className="input h-6 w-auto py-0 text-xs"
                      >
                        {QUESTION_TYPES.map((qt) => (
                          <option key={qt} value={qt}>
                            {questionTypeLabel(qt)}
                          </option>
                        ))}
                      </select>
                      <input
                        {...register(`questions.${i}.weight` as const, { valueAsNumber: true })}
                        type="number"
                        step="0.1"
                        placeholder="weight"
                        className="input h-6 w-20 py-0 text-xs"
                      />
                      {f.is_synthesized && <Badge tone="warn">synth</Badge>}
                      <button
                        type="button"
                        onClick={() => questions.remove(i)}
                        className="ml-auto text-fg-faint hover:text-danger"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                    <textarea
                      {...register(`questions.${i}.text` as const)}
                      rows={2}
                      className="input resize-y text-xs"
                      placeholder="Question text"
                    />
                  </div>
                ))}
                {questions.fields.length === 0 && (
                  <div className="rounded-md border border-dashed border-border-subtle py-4 text-center text-xs text-fg-faint">
                    No questions. Add one so assessors have something to answer.
                  </div>
                )}
              </div>
            </div>

            {/* Attributes (read-only for now) */}
            {control.attributes && Object.keys(control.attributes).length > 0 && (
              <Field label="Attributes (framework-specific)">
                <pre className="max-h-48 overflow-auto rounded-md border border-border-subtle bg-bg-base p-2.5 text-2xs text-fg-muted">
                  {JSON.stringify(control.attributes, null, 2)}
                </pre>
              </Field>
            )}
          </div>

          {/* Footer actions */}
          <div className="flex shrink-0 items-center gap-2 border-t border-border-subtle p-3">
            <Button
              variant="primary"
              size="md"
              onClick={onSave}
              loading={update.isPending}
              disabled={!formState.isDirty}
            >
              <Save className="h-3.5 w-3.5" /> Save changes
            </Button>
            <Button
              variant="ghost"
              size="md"
              type="button"
              disabled={!formState.isDirty}
              onClick={() => reset()}
            >
              <RotateCcw className="h-3.5 w-3.5" /> Revert
            </Button>
            {formState.isDirty && <span className="text-2xs text-warn">Unsaved changes</span>}
            <span className="ml-auto font-mono text-2xs text-fg-faint">#{control.id}</span>
          </div>
        </>
      ) : (
        <HistoryTab pk={control.id} />
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium uppercase tracking-wide text-fg-faint">{label}</label>
      {children}
    </div>
  );
}

function HistoryTab({ pk }: { pk: number }) {
  const { data: history, isLoading } = useControlHistory(pk);
  if (isLoading)
    return (
      <div className="flex flex-1 items-center justify-center">
        <Spinner />
      </div>
    );
  if (!history || history.length === 0)
    return <EmptyState icon={<HistoryIcon className="h-8 w-8" />} title="No edit history yet." />;
  return (
    <div className="flex-1 space-y-2 overflow-y-auto p-4">
      {history.map((h) => (
        <div key={h.id} className="rounded-md border border-border-subtle bg-bg-base p-2.5">
          <div className="flex items-center gap-2">
            <Badge tone={h.action === "delete" ? "danger" : "accent"}>{h.action}</Badge>
            <span className="ml-auto text-2xs text-fg-faint">{relativeTime(h.created_at)}</span>
          </div>
          {h.changes != null && (
            <pre className="mt-2 max-h-40 overflow-auto text-2xs text-fg-muted">
              {JSON.stringify(h.changes, null, 2)}
            </pre>
          )}
        </div>
      ))}
    </div>
  );
}
