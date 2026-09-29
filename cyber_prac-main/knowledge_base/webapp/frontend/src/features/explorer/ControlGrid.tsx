import { useMemo, useState } from "react";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowUpDown, Search } from "lucide-react";
import { useControls } from "@/lib/queries";
import { useUi } from "@/stores/ui";
import { cn } from "@/lib/utils";
import { Badge, Spinner, EmptyState } from "@/components/ui";
import type { ControlListItem } from "@/lib/types";

const col = createColumnHelper<ControlListItem>();

export function ControlGrid() {
  const frameworkId = useUi((s) => s.frameworkId);
  const controlPk = useUi((s) => s.controlPk);
  const selectControl = useUi((s) => s.selectControl);
  const { data: controls, isLoading } = useControls(frameworkId);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [filter, setFilter] = useState("");

  const columns = useMemo(
    () => [
      col.accessor("control_id", {
        header: "Control ID",
        cell: (c) => <span className="font-mono text-xs text-fg">{c.getValue()}</span>,
      }),
      col.accessor("node_code", {
        header: "Node",
        cell: (c) => <span className="font-mono text-2xs text-fg-faint">{c.getValue()}</span>,
      }),
      col.accessor("statement", {
        header: "Statement",
        cell: (c) => <span className="line-clamp-1 text-xs text-fg-muted">{c.getValue()}</span>,
      }),
      col.accessor("question_count", {
        header: "Q",
        cell: (c) =>
          c.getValue() === 0 ? (
            <Badge tone="warn">0</Badge>
          ) : (
            <span className="tabular-nums text-xs text-fg-muted">{c.getValue()}</span>
          ),
      }),
      col.accessor("requires_evidence", {
        header: "Evidence",
        cell: (c) => (c.getValue() ? <Badge tone="accent">req</Badge> : <span className="text-fg-faint">—</span>),
      }),
    ],
    [],
  );

  const table = useReactTable({
    data: controls ?? [],
    columns,
    state: { sorting, globalFilter: filter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setFilter,
    globalFilterFn: "includesString",
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  if (isLoading)
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-border-subtle px-3 py-2">
        <Search className="h-3.5 w-3.5 text-fg-faint" />
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter controls…"
          className="flex-1 bg-transparent text-xs outline-none placeholder:text-fg-faint"
        />
        <span className="text-2xs text-fg-faint">{table.getFilteredRowModel().rows.length} rows</span>
      </div>

      <div className="flex-1 overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-bg-raised">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => (
                  <th
                    key={h.id}
                    onClick={h.column.getToggleSortingHandler()}
                    className="cursor-pointer border-b border-border-subtle px-3 py-2 text-left text-2xs font-medium uppercase tracking-wide text-fg-faint hover:text-fg-muted"
                  >
                    <span className="inline-flex items-center gap-1">
                      {flexRender(h.column.columnDef.header, h.getContext())}
                      {h.column.getCanSort() && <ArrowUpDown className="h-2.5 w-2.5" />}
                    </span>
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                onClick={() => selectControl(row.original.id)}
                className={cn(
                  "cursor-pointer border-b border-border-subtle/50",
                  controlPk === row.original.id ? "bg-accent-subtle" : "hover:bg-bg-hover",
                )}
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-3 py-1.5 align-middle">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {table.getFilteredRowModel().rows.length === 0 && (
          <EmptyState title="No controls match your filter." />
        )}
      </div>
    </div>
  );
}
