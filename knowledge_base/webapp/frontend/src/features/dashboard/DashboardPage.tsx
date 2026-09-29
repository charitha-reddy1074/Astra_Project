import { Link } from "@tanstack/react-router";
import {
  Boxes,
  FileText,
  HelpCircle,
  ClipboardList,
  AlertTriangle,
  Activity,
  ArrowRight,
} from "lucide-react";
import { useStats } from "@/lib/queries";
import { useUi } from "@/stores/ui";
import { Badge, Spinner } from "@/components/ui";
import { relativeTime } from "@/lib/utils";
import type { ReactNode } from "react";

function StatCard({
  icon,
  label,
  value,
  hint,
  tone,
}: {
  icon: ReactNode;
  label: string;
  value: number | string;
  hint?: string;
  tone?: "danger" | "warn";
}) {
  return (
    <div className="rounded-lg border border-border-subtle bg-bg-raised p-4">
      <div className="flex items-center gap-2 text-fg-faint">
        {icon}
        <span className="text-xs">{label}</span>
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <span
          className={
            tone === "danger"
              ? "text-2xl font-semibold text-danger"
              : tone === "warn"
                ? "text-2xl font-semibold text-warn"
                : "text-2xl font-semibold text-fg"
          }
        >
          {value}
        </span>
        {hint && <span className="text-2xs text-fg-faint">{hint}</span>}
      </div>
    </div>
  );
}

export function DashboardPage() {
  const { data, isLoading } = useStats();
  const selectFramework = useUi((s) => s.selectFramework);

  return (
    <>
      <header className="flex h-12 shrink-0 items-center border-b border-border-subtle px-6">
        <h1 className="text-sm font-semibold">Dashboard</h1>
      </header>

      <div className="flex-1 overflow-y-auto p-6">
        {isLoading || !data ? (
          <div className="flex h-40 items-center justify-center">
            <Spinner />
          </div>
        ) : (
          <div className="mx-auto max-w-6xl space-y-6">
            {/* Stat grid */}
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
              <StatCard icon={<Boxes className="h-4 w-4" />} label="Frameworks" value={data.frameworks} />
              <StatCard icon={<FileText className="h-4 w-4" />} label="Nodes" value={data.nodes} />
              <StatCard icon={<FileText className="h-4 w-4" />} label="Controls" value={data.controls} />
              <StatCard icon={<HelpCircle className="h-4 w-4" />} label="Questions" value={data.questions} hint={`${data.synthesized_questions} synth`} />
              <StatCard icon={<ClipboardList className="h-4 w-4" />} label="Assessments" value={data.assessments} />
              <StatCard
                icon={<AlertTriangle className="h-4 w-4" />}
                label="FW with errors"
                value={data.validation_error_frameworks}
                tone={data.validation_error_frameworks > 0 ? "danger" : undefined}
              />
            </div>

            <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
              {/* Frameworks */}
              <div className="lg:col-span-2">
                <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-faint">
                  Frameworks
                </h2>
                <div className="overflow-hidden rounded-lg border border-border-subtle">
                  <table className="w-full text-sm">
                    <thead className="bg-bg-raised text-2xs uppercase tracking-wide text-fg-faint">
                      <tr>
                        <th className="px-3 py-2 text-left font-medium">Framework</th>
                        <th className="px-3 py-2 text-left font-medium">Ver</th>
                        <th className="px-3 py-2 text-right font-medium">Nodes</th>
                        <th className="px-3 py-2 text-right font-medium">Controls</th>
                        <th className="px-3 py-2 text-right font-medium">Questions</th>
                        <th className="px-3 py-2"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.frameworks_detail.map((fw) => (
                        <tr
                          key={fw.id}
                          className="border-t border-border-subtle hover:bg-bg-hover"
                        >
                          <td className="px-3 py-2">
                            <div className="font-medium text-fg">{fw.name}</div>
                            <div className="font-mono text-2xs text-fg-faint">{fw.code}</div>
                          </td>
                          <td className="px-3 py-2">
                            <Badge>{fw.version}</Badge>
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-fg-muted">{fw.node_count}</td>
                          <td className="px-3 py-2 text-right tabular-nums text-fg-muted">{fw.control_count}</td>
                          <td className="px-3 py-2 text-right tabular-nums text-fg-muted">{fw.question_count}</td>
                          <td className="px-3 py-2 text-right">
                            <Link
                              to="/explorer"
                              onClick={() => selectFramework(fw.id)}
                              className="inline-flex items-center gap-1 text-xs text-accent hover:text-accent-hover"
                            >
                              Open <ArrowRight className="h-3 w-3" />
                            </Link>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Activity */}
              <div>
                <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-faint">
                  Recent activity
                </h2>
                <div className="rounded-lg border border-border-subtle bg-bg-raised p-1.5">
                  {data.recent_activity.length === 0 ? (
                    <div className="px-3 py-6 text-center text-xs text-fg-faint">
                      No edits yet. Changes you make appear here.
                    </div>
                  ) : (
                    data.recent_activity.map((a) => (
                      <div
                        key={a.id}
                        className="flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs"
                      >
                        <Activity className="h-3.5 w-3.5 text-fg-faint" />
                        <span className="text-fg-muted">
                          <span className="text-fg">{a.action}</span> {a.entity_type} #{a.entity_id}
                        </span>
                        <span className="ml-auto text-fg-faint">{relativeTime(a.created_at)}</span>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
