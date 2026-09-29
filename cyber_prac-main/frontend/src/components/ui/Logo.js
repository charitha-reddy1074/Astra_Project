"use client";

import { useId } from "react";

/* ═════════════════════════════════════════════════════════════════════════════
   CYBER ASSESSMENT AGENT — BRAND MARK
   ───────────────────────────────────────────────────────────────────────────
   A shield symbol representing security and protection, with a stylized
   circuit pattern inside to represent cyber/tech elements.

   The mark carries the platform's single brand gradient — #F5B700 → #FF6A00 →
   #FF2D8D — as SVG stops. Those three hexes are the ONLY literal colours in the
   product's identity layer; every other surface consumes `var(--…)` tokens.

   <LogoMark size={30} />           full shield mark
   <LogoMark size={30} bare />      outline-only, for dark bands
   <LogoLockup />                   mark + two-line wordmark
   ═════════════════════════════════════════════════════════════════════════════ */

const SEAL_SHADOW = "0 3px 12px -4px rgba(255, 106, 0, 0.42)";

/* Heraldic guard shield — peaked top, tapered sides, rounded point */
const SHIELD =
  "M24 3.2 L40 6.6 Q41 6.85 41 7.9 V24.6 Q41 36.6 24 45 Q7 36.6 7 24.6 V7.9 Q7 6.85 8 6.6 Z";

/* Circuit pattern inside shield */
const CIRCUIT =
  "M12 16h8M12 20h8M16 12v8M20 12v8M14 10h4M14 24h4M10 14v4M10 22v4";

export function LogoMark({ size = 32, bare = false, className = "" }) {
  // useId() is stable across server render and hydration, so the gradient
  // references never mismatch.
  const id = `logo${useId().replace(/[^a-zA-Z0-9]/g, "")}`;

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      role="img"
      aria-label="Cyber Assessment Agent"
      className={className}
      style={{ flex: "none", display: "block", overflow: "visible" }}
    >
      <defs>
        <linearGradient id={`${id}-accent`} x1="0" y1="0" x2="0.85" y2="1">
          <stop offset="0"    stopColor="#F5B700" />
          <stop offset="0.48" stopColor="#FF6A00" />
          <stop offset="1"    stopColor="#FF2D8D" />
        </linearGradient>
      </defs>

      {/* Shield, filled with the brand accent gradient */}
      <path
        d={SHIELD}
        fill={bare ? "none" : `url(#${id}-accent)`}
        stroke={bare ? "var(--brand-orange)" : `url(#${id}-accent)`}
        strokeWidth="2.6"
        strokeLinejoin="round"
      />

      {/* Circuit pattern */}
      <path
        d={CIRCUIT}
        fill="none"
        stroke={bare ? "var(--brand-orange)" : `url(#${id}-accent)`}
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** The mark on its own, carrying the warm drop shadow. */
export function LogoSeal({ size = 34, className = "" }) {
  return (
    <span
      className={`inline-flex shrink-0 transition-transform duration-300 ease-out ${className}`}
      style={{ filter: `drop-shadow(${SEAL_SHADOW})`, lineHeight: 0 }}
    >
      <LogoMark size={size} />
    </span>
  );
}

/**
 * Full lockup: mark + two-line wordmark.
 * `tone="light"` renders for dark backgrounds.
 * `stacked={false}` keeps the name on one line (wide headers, hero areas).
 */
export function LogoLockup({
  size = 34,
  tone = "dark",
  stacked = true,
  className = "",
}) {
  const light = tone === "light";
  const fs = size * 0.44;

  return (
    <div className={`group flex items-center gap-2.5 select-none ${className}`}>
      <LogoSeal size={size} className="group-hover:-translate-y-px" />
      <span
        className={`font-display font-extrabold tracking-tight min-w-0 ${
          light ? "text-white" : "text-slate-900"
        }`}
        style={{ fontSize: fs, letterSpacing: "-0.035em", lineHeight: 1.12 }}
      >
        {stacked ? (
          <>
            <span className="block">Cyber Assessment</span>
            <span style={{ color: "var(--accent-ink)" }}>Agent</span>
          </>
        ) : (
          <>
            Cyber Assessment{" "}
            <span style={{ color: "var(--accent-ink)" }}>Agent</span>
          </>
        )}
      </span>
    </div>
  );
}

export default LogoMark;