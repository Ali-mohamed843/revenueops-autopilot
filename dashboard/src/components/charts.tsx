"use client";

/*
 * Charts in the design's style: thin rings and lines, quiet gridlines, legends that carry the exact
 * values (so no number is behind a hover only), and a hover/focus readout on every chart.
 */

import Link from "next/link";
import { useId, useLayoutEffect, useRef, useState } from "react";

import { cn } from "./ui";
import { count, moneyCompact } from "@/lib/format";
import { niceTicks } from "@/lib/scale";

/** How a chart prints its numbers. A name, not a function: charts are client components. */
export type ValueFormat = "count" | "money";
const FORMATS: Record<ValueFormat, (n: number) => string> = {
  count: (n) => count(n),
  money: (n) => moneyCompact(n, ""),
};

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

// -------------------------------------------------------------------- donut

export interface Slice {
  label: string;
  value: number;
  display: string; // shown in the legend, e.g. "71.2K"
  color: string; // a CSS variable
  href?: string;
}

const R = 74;
const C = 2 * Math.PI * R;

export function Donut({
  slices,
  center,
  centerLabel,
  label,
  footer,
}: {
  slices: Slice[];
  center: string;
  centerLabel: string;
  label: string;
  footer?: React.ReactNode;
}) {
  const [active, setActive] = useState<number | null>(null);
  const total = slices.reduce((a, s) => a + s.value, 0);
  let acc = 0;
  const arcs = slices.map((s) => {
    const len = total ? (s.value / total) * C : 0;
    const arc = { dash: `${Math.max(len - (slices.length > 1 ? 4 : 0), 0.5)} ${C}`, offset: -acc };
    acc += len;
    return arc;
  });
  const pct = (v: number) => (total ? `${Math.round((v / total) * 100)}%` : "—");

  return (
    <div className="flex flex-wrap items-center gap-7">
      <div className="relative size-[168px] shrink-0">
        <svg viewBox="0 0 180 180" width={168} height={168} role="img" aria-label={label}>
          <circle cx={90} cy={90} r={R} fill="none" stroke="var(--track)" strokeWidth={12} />
          {total > 0 &&
            slices.map((s, i) => (
              <circle
                key={s.label}
                cx={90}
                cy={90}
                r={R}
                fill="none"
                stroke={s.color}
                strokeWidth={active === i ? 15 : 12}
                strokeDasharray={arcs[i].dash}
                strokeDashoffset={arcs[i].offset}
                transform="rotate(-90 90 90)"
                opacity={active === null || active === i ? 1 : 0.35}
                className="transition-[opacity,stroke-width]"
              />
            ))}
        </svg>
        <div className="pointer-events-none absolute inset-0 grid place-items-center text-center">
          <div>
            <p className="serif num text-[28px] leading-tight font-light text-ink">
              {active === null ? center : slices[active].display}
            </p>
            <p className="mt-0.5 max-w-24 truncate text-[12px] text-muted">
              {active === null ? centerLabel : slices[active].label}
            </p>
          </div>
        </div>
      </div>
      <div className="min-w-0 flex-[1_1_170px]">
        {slices.map((s, i) => {
          const row = (
            <>
              <span className="size-2 rounded-[2px]" style={{ background: s.color }} aria-hidden />
              <span className="truncate">{s.label}</span>
              <span className="num text-ink">{s.display}</span>
              <span className="num w-[34px] text-end text-[12px] text-muted">{pct(s.value)}</span>
            </>
          );
          const props = {
            className:
              "grid grid-cols-[8px_minmax(0,1fr)_auto_auto] items-center gap-2.5 border-t border-line-3 py-[7px] text-[13px] text-ink-2 hover:text-ink",
            onMouseEnter: () => setActive(i),
            onMouseLeave: () => setActive(null),
            onFocus: () => setActive(i),
            onBlur: () => setActive(null),
          };
          return s.href ? (
            <Link key={s.label} href={s.href} {...props}>
              {row}
            </Link>
          ) : (
            <div key={s.label} tabIndex={0} {...props}>
              {row}
            </div>
          );
        })}
        {footer}
      </div>
    </div>
  );
}

// --------------------------------------------------------------- line chart

export interface Series {
  key: string;
  label: string;
  color: string;
}

export function LineChart({
  data,
  series,
  format: formatName,
  label,
  height = 220,
}: {
  data: { label: string; values: Record<string, number> }[];
  series: Series[];
  format: ValueFormat;
  label: string;
  height?: number;
}) {
  const format = FORMATS[formatName];
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const titleId = useId();
  const max = Math.max(0, ...data.flatMap((d) => series.map((s) => d.values[s.key] ?? 0)));
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1];
  const pad = { left: 44, right: 8, top: 8, bottom: 26 };
  const plotW = Math.max(width - pad.left - pad.right, 10);
  const plotH = height - pad.top - pad.bottom;
  const x = (i: number) => pad.left + (data.length > 1 ? (i / (data.length - 1)) * plotW : plotW / 2);
  const y = (v: number) => pad.top + plotH - (v / top) * plotH;
  const every = Math.max(1, Math.ceil(data.length / Math.max(Math.floor(plotW / 72), 1)));

  const onMove = (clientX: number) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box || data.length === 0) return;
    const rel = (clientX - box.left - pad.left) / plotW;
    setHover(Math.max(0, Math.min(data.length - 1, Math.round(rel * (data.length - 1)))));
  };

  return (
    <div
      ref={ref}
      className="relative w-full"
      onMouseMove={(e) => onMove(e.clientX)}
      onMouseLeave={() => setHover(null)}
      tabIndex={0}
      aria-label={`${label}. Use the arrow keys to read each day.`}
      onKeyDown={(e) => {
        if (e.key === "ArrowRight") setHover((h) => Math.min(data.length - 1, (h ?? -1) + 1));
        if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? data.length) - 1));
      }}
      onBlur={() => setHover(null)}
    >
      {hover !== null && (
        <div
          role="status"
          className="pointer-events-none absolute z-10 min-w-36 rounded-[10px] border border-line bg-card px-3 py-2 shadow-pop"
          style={{ left: Math.min(Math.max(x(hover), 80), width - 80), top: 0, transform: "translate(-50%, -100%)" }}
        >
          <p className="mb-1 text-[12px] text-muted">{data[hover].label}</p>
          {series.map((s) => (
            <p key={s.key} className="flex items-center gap-2 text-[13px]">
              <span className="h-0.5 w-3" style={{ background: s.color }} aria-hidden />
              <span className="num font-medium text-ink">{format(data[hover].values[s.key] ?? 0)}</span>
              <span className="text-ink-2">{s.label}</span>
            </p>
          ))}
        </div>
      )}
      {width > 0 ? (
        <svg width={width} height={height} role="img" aria-labelledby={titleId} className="block overflow-visible">
          <title id={titleId}>{label}</title>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} stroke={t === 0 ? "var(--btn-line)" : "var(--line-2)"} />
              <text x={pad.left - 10} y={y(t)} dy="0.32em" textAnchor="end" className="num fill-muted text-[11px]">
                {format(t)}
              </text>
            </g>
          ))}
          {data.map(
            (d, i) =>
              i % every === 0 && (
                <text key={d.label} x={x(i)} y={height - 6} textAnchor="middle" className="num fill-muted text-[12px]">
                  {d.label}
                </text>
              ),
          )}
          {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={pad.top} y2={pad.top + plotH} stroke="var(--btn-line)" />}
          {series.map((s) => (
            <g key={s.key}>
              <polyline
                points={data.map((d, i) => `${x(i)},${y(d.values[s.key] ?? 0)}`).join(" ")}
                fill="none"
                stroke={s.color}
                strokeWidth={1.75}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              {hover !== null && (
                <circle cx={x(hover)} cy={y(data[hover].values[s.key] ?? 0)} r={4} fill={s.color} stroke="var(--card)" strokeWidth={2} />
              )}
            </g>
          ))}
        </svg>
      ) : (
        <div style={{ height }} />
      )}
      <table className="sr-only">
        <caption>{label}</caption>
        <thead>
          <tr>
            <th>Day</th>
            {series.map((s) => (
              <th key={s.key}>{s.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((d) => (
            <tr key={d.label}>
              <td>{d.label}</td>
              {series.map((s) => (
                <td key={s.key}>{format(d.values[s.key] ?? 0)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- sparkline

/** The hero's quiet trend line. Decorative: the numbers are printed beside it. */
export function Sparkline({ values, className }: { values: number[]; className?: string }) {
  if (values.length < 2) return null;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const d = values
    .map((v, i) => `${i ? "L" : "M"}${((i / (values.length - 1)) * 600).toFixed(1)} ${(76 - ((v - min) / span) * 70).toFixed(1)}`)
    .join(" ");
  return (
    <svg viewBox="0 0 600 80" preserveAspectRatio="none" className={cn("block h-[72px] w-full", className)} aria-hidden>
      <path d={d} fill="none" stroke="var(--spark)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
