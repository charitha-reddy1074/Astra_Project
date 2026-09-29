import { AlertTriangle, CheckCircle2, ShieldCheck, XCircle } from "lucide-react";
import { useFrameworks, useValidation } from "@/lib/queries";
import { useUi } from "@/stores/ui";
import { useNavigate } from "@tanstack/react-router";
import { Badge, EmptyState, Spinner } from "@/components/ui";
import { cn } from "@/lib/utils";

export function ValidationPage() {
  const frameworkId = useUi((s) => s.frameworkId);
  const selectFramework = useUi((s) => s.selectFramework);
  const selectControl = useUi((s) => s.selectControl);
  const navigate = useNavigate();
  const { data: frameworks } = useFrameworks();
  const { data: report, isLoading } = useValidation(frameworkId);

  return (
    <>
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-border-subtle px-6">
        <ShieldCheck className="h-4 w-4 text-fg-faint" />
        <h1 className="text-sm font-semibold">Validation</h1>
        <select
          value={frameworkId ?? ""}
          onChange={(e) => selectFramework(Number(e.target.value))}
          className="input ml-3 h-7 w-56 py-0 text-xs"
        >
          <option value="" disabled>
            Select a framework…
          </option>
          {frameworks?.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name} v{f.version}
            </option>
          ))}
        </select>
      </header>

      <div className="flex-1 overflow-y-auto p-6">
        {frameworkId == null ? (
          <EmptyState title="Select a framework to validate." />
        ) : isLoading || !report ? (
          <div className="flex h-40 items-center justify-center">
            <Spinner />
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-4">
            <div
              className={cn(
                "flex items-center gap-3 rounded-lg border p-4",
                report.ok
                  ? "border-ok/30 bg-ok/5"
                  : "border-danger/30 bg-danger/5",
              )}
            >
              {report.ok ? (
                <CheckCircle2 className="h-6 w-6 text-ok" />
              ) : (
                <XCircle className="h-6 w-6 text-danger" />
              )}
              <div>
                <div className="text-sm font-medium">
                  {report.ok ? "Framework is valid" : "Validation found issues"}
                </div>
                <div className="text-xs text-fg-muted">
                  {report.error_count} errors · {report.warning_count} warnings
                </div>
              </div>
            </div>

            {report.issues.length === 0 ? (
              <EmptyState
                icon={<CheckCircle2 className="h-8 w-8 text-ok" />}
                title="No issues detected."
              />
            ) : (
              <div className="space-y-1.5">
                {report.issues.map((issue, i) => (
                  <div
                    key={i}
                    onClick={() => {
                      if (issue.entity_type === "control" && issue.entity_id) {
                        selectControl(issue.entity_id);
                        navigate({ to: "/explorer" });
                      }
                    }}
                    className={cn(
                      "flex items-start gap-3 rounded-md border border-border-subtle bg-bg-raised p-3",
                      issue.entity_type === "control" && issue.entity_id && "cursor-pointer hover:bg-bg-hover",
                    )}
                  >
                    {issue.severity === "error" ? (
                      <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
                    ) : (
                      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="text-sm text-fg">{issue.message}</div>
                      <div className="mt-0.5 flex items-center gap-2 text-2xs text-fg-faint">
                        <Badge tone={issue.severity === "error" ? "danger" : "warn"}>
                          {issue.rule}
                        </Badge>
                        <span>
                          {issue.entity_type}
                          {issue.entity_ref ? ` · ${issue.entity_ref}` : ""}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}
