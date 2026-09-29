import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { ListTree, Table2 } from "lucide-react";
import { useUi } from "@/stores/ui";
import { useFrameworks } from "@/lib/queries";
import { cn } from "@/lib/utils";
import { FrameworkRail } from "./FrameworkRail";
import { TreeView } from "./TreeView";
import { ControlGrid } from "./ControlGrid";
import { DetailPanel } from "./DetailPanel";
import { EmptyState } from "@/components/ui";
import { FolderTree } from "lucide-react";

export function ExplorerPage() {
  const frameworkId = useUi((s) => s.frameworkId);
  const viewMode = useUi((s) => s.viewMode);
  const setViewMode = useUi((s) => s.setViewMode);
  const { data: frameworks } = useFrameworks();
  const activeFw = frameworks?.find((f) => f.id === frameworkId);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Header / breadcrumb */}
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-border-subtle px-4">
        <FolderTree className="h-4 w-4 text-fg-faint" />
        <span className="text-sm font-semibold">Explorer</span>
        {activeFw && (
          <>
            <span className="text-fg-faint">/</span>
            <span className="text-sm text-fg-muted">{activeFw.name}</span>
            <span className="rounded bg-bg-hover px-1.5 py-0.5 text-2xs text-fg-faint">
              v{activeFw.version}
            </span>
          </>
        )}

        <div className="ml-auto flex items-center rounded-md border border-border-subtle p-0.5">
          {(["tree", "grid"] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => setViewMode(mode)}
              className={cn(
                "flex items-center gap-1.5 rounded px-2 py-1 text-xs transition-colors",
                viewMode === mode
                  ? "bg-bg-hover text-fg"
                  : "text-fg-faint hover:text-fg-muted",
              )}
            >
              {mode === "tree" ? <ListTree className="h-3.5 w-3.5" /> : <Table2 className="h-3.5 w-3.5" />}
              {mode === "tree" ? "Tree" : "Grid"}
            </button>
          ))}
        </div>
      </header>

      {/* 3-pane resizable body */}
      <PanelGroup direction="horizontal" className="min-h-0 flex-1" autoSaveId="explorer-panels">
        <Panel defaultSize={16} minSize={12} maxSize={24}>
          <FrameworkRail />
        </Panel>
        <ResizeHandle />
        <Panel defaultSize={44} minSize={28}>
          {frameworkId == null ? (
            <EmptyState
              icon={<FolderTree className="h-8 w-8" />}
              title="Select a framework"
              hint="Choose a framework on the left to browse its hierarchy and controls."
            />
          ) : viewMode === "tree" ? (
            <TreeView />
          ) : (
            <ControlGrid />
          )}
        </Panel>
        <ResizeHandle />
        <Panel defaultSize={40} minSize={28}>
          <DetailPanel />
        </Panel>
      </PanelGroup>
    </div>
  );
}

function ResizeHandle() {
  return (
    <PanelResizeHandle className="w-px bg-border-subtle transition-colors data-[resize-handle-state=hover]:bg-accent data-[resize-handle-state=drag]:bg-accent" />
  );
}
