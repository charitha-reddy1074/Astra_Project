import { clsx, type ClassValue } from "clsx";

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs);
}

const QT_LABELS: Record<string, string> = {
  YES_NO: "Yes / No",
  FREE_TEXT: "Free text",
  MULTI_CHOICE: "Multi-choice",
  MATURITY_SCALE: "Maturity scale",
  NUMERIC: "Numeric",
};

export const questionTypeLabel = (t: string) => QT_LABELS[t] ?? t;

export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diff = Date.now() - then;
  const min = Math.floor(diff / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}
