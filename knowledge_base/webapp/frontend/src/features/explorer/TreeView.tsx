import { useMemo } from "react";
import { ChevronRight, FileText, ShieldCheck } from "lucide-react";
import { useControls, useTree } from "@/lib/queries";
import { useUi } from "@/stores/ui";
import { cn } from "@/lib/utils";
import { Spinner, EmptyState } from "@/components/ui";
import type { ControlListItem, TreeNode } from "@/lib/types";

export function TreeView() {
  const frameworkId = useUi((s) => s.frameworkId);
  const { data: tree, isLoading } = useTree(frameworkId);
  const { data: controls } = useControls(frameworkId);

  const controlsByNode = useMemo(() => {
    const map = new Map<number, ControlListItem[]>();
    (controls ?? []).forEach((c) => {
      const list = map.get(c.node_id) ?? [];
      list.push(c);
      map.set(c.node_id, list);
    });
    return map;
  }, [controls]);

  if (isLoading)
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  if (!tree || tree.length === 0)
    return <EmptyState title="This framework has no hierarchy nodes." />;

  return (
    <div className="h-full overflow-y-auto py-1.5 text-sm">
      {tree.map((node) => (
        <NodeRow key={node.id} node={node} depth={0} controlsByNode={controlsByNode} />
      ))}
    </div>
  );
}

function NodeRow({
  node,
  depth,
  controlsByNode,
}: {
  node: TreeNode;
  depth: number;
  controlsByNode: Map<number, ControlListItem[]>;
}) {
  const expanded = useUi((s) => s.expanded[node.id] ?? depth === 0);
  const toggle = useUi((s) => s.toggleNode);
  const nodeControls = controlsByNode.get(node.id) ?? [];
  const hasChildren = node.children.length > 0 || nodeControls.length > 0;

  return (
    <div>
      <button
        onClick={() => toggle(node.id)}
        className="group flex w-full items-center gap-1 rounded px-2 py-1 hover:bg-bg-hover"
        style={{ paddingLeft: depth * 14 + 8 }}
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-fg-faint transition-transform",
            expanded && "rotate-90",
            !hasChildren && "opacity-0",
          )}
        />
        <span className="font-mono text-xs text-fg-muted group-hover:text-fg">{node.code}</span>
        <span className="truncate text-xs text-fg-faint">{node.name !== node.code ? node.name : ""}</span>
        <span className="ml-auto pr-1 text-2xs text-fg-faint">
          {node.descendant_control_count}
        </span>
      </button>

      {expanded && (
        <div className="animate-fade-in">
          {node.children.map((child) => (
            <NodeRow
              key={child.id}
              node={child}
              depth={depth + 1}
              controlsByNode={controlsByNode}
            />
          ))}
          {nodeControls.map((c) => (
            <ControlLeaf key={c.id} control={c} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

function ControlLeaf({ control, depth }: { control: ControlListItem; depth: number }) {
  const controlPk = useUi((s) => s.controlPk);
  const selectControl = useUi((s) => s.selectControl);
  const active = controlPk === control.id;

  return (
    <button
      onClick={() => selectControl(control.id)}
      className={cn(
        "flex w-full items-center gap-1.5 rounded px-2 py-1 text-left",
        active ? "bg-accent-subtle" : "hover:bg-bg-hover",
      )}
      style={{ paddingLeft: depth * 14 + 22 }}
    >
      <FileText className={cn("h-3.5 w-3.5 shrink-0", active ? "text-accent" : "text-fg-faint")} />
      <span className={cn("font-mono text-xs", active ? "text-accent" : "text-fg-muted")}>
        {control.control_id}
      </span>
      <span className="truncate text-xs text-fg-faint">{control.name ?? control.statement}</span>
      {control.requires_evidence && (
        <ShieldCheck className="ml-auto h-3 w-3 shrink-0 text-fg-faint" />
      )}
    </button>
  );
}
