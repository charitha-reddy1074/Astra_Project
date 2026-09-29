import { useFrameworks } from "@/lib/queries";
import { useUi } from "@/stores/ui";
import { cn } from "@/lib/utils";
import { Spinner } from "@/components/ui";

export function FrameworkRail() {
  const { data: frameworks, isLoading } = useFrameworks();
  const frameworkId = useUi((s) => s.frameworkId);
  const selectFramework = useUi((s) => s.selectFramework);

  return (
    <div className="flex h-full flex-col border-r border-border-subtle bg-bg-raised">
      <div className="px-3 py-2 text-2xs font-medium uppercase tracking-wide text-fg-faint">
        Frameworks
      </div>
      <div className="flex-1 overflow-y-auto px-1.5 pb-2">
        {isLoading && (
          <div className="flex justify-center py-4">
            <Spinner />
          </div>
        )}
        {frameworks?.map((fw) => {
          const active = fw.id === frameworkId;
          return (
            <button
              key={fw.id}
              onClick={() => selectFramework(fw.id)}
              className={cn(
                "mb-0.5 flex w-full flex-col items-start rounded-md px-2.5 py-1.5 text-left transition-colors",
                active ? "bg-bg-hover" : "hover:bg-bg-hover/60",
              )}
            >
              <div className="flex w-full items-center gap-1.5">
                <span
                  className={cn(
                    "h-1.5 w-1.5 rounded-full",
                    active ? "bg-accent" : "bg-border-strong",
                  )}
                />
                <span className={cn("text-sm", active ? "text-fg" : "text-fg-muted")}>
                  {fw.name}
                </span>
              </div>
              <span className="pl-3 text-2xs text-fg-faint">
                {fw.control_count} controls · v{fw.version}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
