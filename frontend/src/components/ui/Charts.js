"use client";

import { useEffect, useState, useRef, useId } from "react";

// ── Donut Chart ──────────────────────────────────────────────────────────────
export function DonutChart({ segments, size = 120, thickness = 16, children }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => { const t = setTimeout(() => setMounted(true), 120); return () => clearTimeout(t); }, []);

  const total  = segments.reduce((s, d) => s + d.value, 0) || 1;
  const r      = (size - thickness) / 2;
  const circ   = 2 * Math.PI * r;
  const cx = size / 2, cy = size / 2;

  let accumulated = 0;
  const slices = segments.map(seg => {
    const len    = (seg.value / total) * circ;
    const offset = -accumulated;
    accumulated += len;
    return { ...seg, len: mounted ? len : 0, offset };
  });

  return (
    <div className="relative inline-flex items-center justify-center shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}
        style={{ transform: "rotate(-90deg)", transformOrigin: "center" }}>
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="#e2e8f0" strokeWidth={thickness} />
        {slices.map((s, i) => s.len > 0 && (
          <circle key={i} cx={cx} cy={cy} r={r}
            fill="none" stroke={s.color}
            strokeWidth={thickness - 2}
            strokeDasharray={`${s.len} ${circ}`}
            strokeDashoffset={s.offset}
            strokeLinecap="butt"
            style={{ transition: "stroke-dasharray 0.9s cubic-bezier(0.4,0,0.2,1)" }}
          />
        ))}
      </svg>
      {children && (
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          {children}
        </div>
      )}
    </div>
  );
}

// ── Vertical Bar Chart ───────────────────────────────────────────────────────
export function BarChart({ data, height = 100 }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => { const t = setTimeout(() => setMounted(true), 180); return () => clearTimeout(t); }, []);

  const max = Math.max(...data.map(d => d.value), 1);

  return (
    <div className="flex items-end justify-between gap-2" style={{ height }}>
      {data.map((d, i) => {
        const pct = mounted ? Math.max((d.value / max) * 88, d.value > 0 ? 4 : 0) : 0;
        return (
          <div key={i} className="flex-1 h-full flex flex-col items-center gap-1.5 justify-end">
            {d.value > 0 && <span className="text-[10px] font-bold text-slate-600 shrink-0">{d.value}</span>}
            <div className="w-full shrink-0 rounded-t-md transition-all duration-700 ease-out"
              style={{ height: `${pct}%`, background: d.color, minHeight: 0 }} />
            <span className="text-[9px] text-slate-400 truncate w-full text-center leading-tight">{d.label}</span>
          </div>
        );
      })}
    </div>
  );
}

// ── Radar Chart ──────────────────────────────────────────────────────────────
export function RadarChart({ domains, size = 180 }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => { const t = setTimeout(() => setMounted(true), 220); return () => clearTimeout(t); }, []);

  if (!domains || domains.length < 3) return null;
  const n = domains.length;
  const cx = size / 2, cy = size / 2, r = size / 2 - 24;
  const angles = domains.map((_, i) => ((i / n) * 2 * Math.PI) - Math.PI / 2);

  const gridPts = (level) => angles.map(a => `${cx + r * level * Math.cos(a)},${cy + r * level * Math.sin(a)}`).join(" ");

  const dataPoints = mounted
    ? domains.map((d, i) => ({ x: cx + r * Math.min(d.score / 100, 1) * Math.cos(angles[i]), y: cy + r * Math.min(d.score / 100, 1) * Math.sin(angles[i]) }))
    : domains.map(() => ({ x: cx, y: cy }));

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      {[0.25, 0.5, 0.75, 1].map(l => <polygon key={l} points={gridPts(l)} fill="none" stroke="#e2e8f0" strokeWidth="0.75" />)}
      {angles.map((a, i) => <line key={i} x1={cx} y1={cy} x2={cx + r * Math.cos(a)} y2={cy + r * Math.sin(a)} stroke="#e2e8f0" strokeWidth="0.75" />)}
      <polygon
        points={dataPoints.map(p => `${p.x},${p.y}`).join(" ")}
        fill="rgba(59,130,246,0.12)" stroke="#3b82f6" strokeWidth="1.5"
        style={{ transition: "all 0.7s cubic-bezier(0.4,0,0.2,1)" }}
      />
      {dataPoints.map((p, i) => (
        <circle key={i} cx={p.x} cy={p.y} r="3" fill="#3b82f6" stroke="white" strokeWidth="1.5"
          style={{ transition: "all 0.7s cubic-bezier(0.4,0,0.2,1)" }} />
      ))}
      {domains.map((d, i) => {
        const lr = r + 14;
        const x = cx + lr * Math.cos(angles[i]);
        const y = cy + lr * Math.sin(angles[i]);
        const anchor = Math.abs(Math.cos(angles[i])) < 0.15 ? "middle" : Math.cos(angles[i]) < 0 ? "end" : "start";
        return <text key={i} x={x} y={y} textAnchor={anchor} dominantBaseline="middle" fontSize="7.5" fill="#64748b" fontWeight="600">{d.code}</text>;
      })}
    </svg>
  );
}

// ── Area Chart (score trend) ─────────────────────────────────────────────────
// data: [{ label, value }] — value scaled on a 0..max axis. Renders a faint
// grid, a gradient area, a stroked line and value/dot markers. Lightweight,
// dependency-free SVG (matches the Donut/Bar/Radar family above).
export function AreaChart({
  data,
  height = 220,
  max = 100,
  suffix = "%",
  color = "#2563eb",
}) {
  const uid        = useId();
  const [mounted, setMounted] = useState(false);
  useEffect(() => { const t = setTimeout(() => setMounted(true), 160); return () => clearTimeout(t); }, []);

  if (!data || data.length === 0) return null;

  const W = 640, H = 200;
  const PAD_X = 8, PAD_T = 16, PAD_B = 26;
  const n       = data.length;
  const maxVal  = max || Math.ceil(Math.max(...data.map(d => d.value), 1) / 10) * 10;
  const stepX   = n > 1 ? (W - PAD_X * 2) / (n - 1) : 0;
  const px = (i) => PAD_X + i * stepX;
  const py = (v) => H - PAD_B - (Math.min(Math.max(v, 0), maxVal) / maxVal) * (H - PAD_B - PAD_T);

  const coords    = data.map((d, i) => [px(i), py(d.value)]);
  const linePath  = coords.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const bottomY   = (H - PAD_B).toFixed(1);
  const areaPath  = `${linePath} L${px(n - 1).toFixed(1)},${bottomY} L${px(0).toFixed(1)},${bottomY} Z`;
  const showValues = n <= 12;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="block w-full" style={{ height }} role="img" aria-label="Compliance score trend">
      <defs>
        <linearGradient id={`${uid}-fill`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.20" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>

      {[0, 0.25, 0.5, 0.75, 1].map(t => {
        const y = py(maxVal * t);
        return (
          <g key={t}>
            <line x1={PAD_X} y1={y} x2={W - PAD_X} y2={y} stroke="#e2e8f0" strokeWidth="1" strokeDasharray="3 4" />
            <text x={W - PAD_X} y={y - 3} textAnchor="end" fontSize="8" fill="#94a3b8" fontWeight="600">{Math.round(maxVal * t)}{suffix}</text>
          </g>
        );
      })}

      <path d={areaPath} fill={`url(#${uid}-fill)`} style={{ opacity: mounted ? 1 : 0, transition: "opacity 0.7s ease" }} />
      <path d={linePath} fill="none" stroke={color} strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round"
        style={{ opacity: mounted ? 1 : 0, transition: "opacity 0.5s ease" }} />

      {mounted && coords.map(([x, y], i) => (
        <g key={i}>
          <circle cx={x} cy={y} r="3.5" fill="#ffffff" stroke={color} strokeWidth="2" />
          {showValues && (
            <text x={x} y={y - 9} textAnchor="middle" fontSize="8.5" fill="#475569" fontWeight="700">
              {Math.round(data[i].value)}{suffix}
            </text>
          )}
        </g>
      ))}

      {data.map((d, i) => (
        <text key={i} x={px(i)} y={H - 8} textAnchor="middle" fontSize="8.5" fill="#94a3b8" fontWeight="600">{d.label}</text>
      ))}
    </svg>
  );
}

// ── Count-Up ─────────────────────────────────────────────────────────────────
export function CountUp({ to, duration = 900, suffix = "" }) {
  const [val, setVal] = useState(0);
  const raf = useRef(null);

  useEffect(() => {
    if (to == null) return;
    const start = performance.now();
    const tick = (now) => {
      const p = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      setVal(Math.round(eased * to));
      if (p < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => { if (raf.current) cancelAnimationFrame(raf.current); };
  }, [to, duration]);

  return <>{val}{suffix}</>;
}
