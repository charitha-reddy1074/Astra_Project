// Small, hand-rolled primitives styled for the dark enterprise aesthetic.
import { create } from "zustand";
import { Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/utils";

// --- Button ------------------------------------------------------------------
type Variant = "primary" | "ghost" | "outline" | "danger";
type Size = "sm" | "md" | "icon";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-white hover:bg-accent-hover",
  ghost: "text-fg-muted hover:text-fg hover:bg-bg-hover",
  outline: "border border-border-strong text-fg hover:bg-bg-hover",
  danger: "text-danger hover:bg-danger/10",
};
const SIZES: Record<Size, string> = {
  sm: "h-7 px-2.5 text-xs gap-1.5",
  md: "h-8 px-3 text-sm gap-2",
  icon: "h-7 w-7 justify-center",
};

export function Button({
  variant = "ghost",
  size = "md",
  loading,
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}) {
  return (
    <button
      className={cn(
        "inline-flex items-center rounded-md font-medium transition-colors",
        "focus-visible:focus-ring disabled:opacity-50 disabled:pointer-events-none",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      disabled={loading || props.disabled}
      {...props}
    >
      {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      {children}
    </button>
  );
}

// --- Badge -------------------------------------------------------------------
export function Badge({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: "neutral" | "accent" | "warn" | "danger" | "ok";
  className?: string;
}) {
  const tones = {
    neutral: "bg-bg-hover text-fg-muted border-border-subtle",
    accent: "bg-accent-subtle text-accent border-accent/30",
    warn: "bg-warn/10 text-warn border-warn/30",
    danger: "bg-danger/10 text-danger border-danger/30",
    ok: "bg-ok/10 text-ok border-ok/30",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border px-1.5 py-0.5 text-2xs font-medium",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

// --- Spinner / Empty ---------------------------------------------------------
export const Spinner = ({ className }: { className?: string }) => (
  <Loader2 className={cn("h-4 w-4 animate-spin text-fg-faint", className)} />
);

export function EmptyState({
  icon,
  title,
  hint,
}: {
  icon?: ReactNode;
  title: string;
  hint?: string;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      {icon && <div className="text-fg-faint">{icon}</div>}
      <div className="text-sm text-fg-muted">{title}</div>
      {hint && <div className="max-w-xs text-xs text-fg-faint">{hint}</div>}
    </div>
  );
}

// --- Toast -------------------------------------------------------------------
type Toast = { id: number; message: string; tone: "ok" | "danger" | "neutral" };
interface ToastState {
  toasts: Toast[];
  push: (message: string, tone?: Toast["tone"]) => void;
  dismiss: (id: number) => void;
}
let toastId = 0;
export const useToasts = create<ToastState>((set) => ({
  toasts: [],
  push: (message, tone = "neutral") => {
    const id = ++toastId;
    set((s) => ({ toasts: [...s.toasts, { id, message, tone }] }));
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), 3500);
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

export function Toaster() {
  const { toasts, dismiss } = useToasts();
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          onClick={() => dismiss(t.id)}
          className={cn(
            "pointer-events-auto cursor-pointer rounded-lg border px-3.5 py-2.5 text-sm shadow-lg animate-slide-up",
            "bg-bg-panel backdrop-blur",
            t.tone === "ok" && "border-ok/40 text-ok",
            t.tone === "danger" && "border-danger/40 text-danger",
            t.tone === "neutral" && "border-border-strong text-fg",
          )}
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}
