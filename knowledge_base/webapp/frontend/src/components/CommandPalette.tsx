import { useState } from "react";
import { Command } from "cmdk";
import { useNavigate } from "@tanstack/react-router";
import { Search, FileText, Box } from "lucide-react";
import { useUi } from "@/stores/ui";
import { useSearch } from "@/lib/queries";
import { Badge } from "@/components/ui";

export function CommandPalette() {
  const open = useUi((s) => s.commandOpen);
  const setOpen = useUi((s) => s.setCommandOpen);
  const selectFramework = useUi((s) => s.selectFramework);
  const selectControl = useUi((s) => s.selectControl);
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const { data: hits = [], isFetching } = useSearch(q);

  const close = () => {
    setOpen(false);
    setQ("");
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 pt-[15vh] animate-fade-in"
      onClick={close}
    >
      <Command
        shouldFilter={false}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-xl overflow-hidden rounded-xl border border-border-strong bg-bg-panel shadow-2xl animate-slide-up"
      >
        <div className="flex items-center gap-2 border-b border-border-subtle px-3.5">
          <Search className="h-4 w-4 text-fg-faint" />
          <Command.Input
            autoFocus
            value={q}
            onValueChange={setQ}
            placeholder="Search controls by ID, statement, question…"
            className="h-11 flex-1 bg-transparent text-sm outline-none placeholder:text-fg-faint"
          />
          {isFetching && <span className="text-2xs text-fg-faint">searching…</span>}
        </div>
        <Command.List className="max-h-80 overflow-y-auto p-1.5">
          {q.trim() === "" && (
            <div className="px-3 py-6 text-center text-xs text-fg-faint">
              Type to search across every framework.
            </div>
          )}
          {q.trim() !== "" && hits.length === 0 && !isFetching && (
            <Command.Empty className="px-3 py-6 text-center text-xs text-fg-faint">
              No matches for “{q}”.
            </Command.Empty>
          )}
          {hits.map((hit) => (
            <Command.Item
              key={hit.control_pk}
              value={`${hit.control_id}-${hit.control_pk}`}
              onSelect={() => {
                selectFramework(hit.framework_id);
                selectControl(hit.control_pk);
                navigate({ to: "/explorer" });
                close();
              }}
              className="flex cursor-pointer items-center gap-3 rounded-md px-2.5 py-2 text-sm data-[selected=true]:bg-bg-hover"
            >
              <FileText className="h-4 w-4 shrink-0 text-fg-faint" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs text-fg">{hit.control_id}</span>
                  <Badge tone="neutral">{hit.framework_code}</Badge>
                </div>
                <div className="truncate text-xs text-fg-muted">{hit.statement}</div>
              </div>
              <Badge tone="accent">{hit.match_field}</Badge>
            </Command.Item>
          ))}
        </Command.List>
        <div className="flex items-center gap-3 border-t border-border-subtle px-3 py-1.5 text-2xs text-fg-faint">
          <Box className="h-3 w-3" />
          <span>↑↓ navigate · ↵ open · esc close</span>
        </div>
      </Command>
    </div>
  );
}
