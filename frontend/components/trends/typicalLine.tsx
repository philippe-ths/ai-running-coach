// #413: shared styling for the dashed "typical" reference line drawn on the
// single-series trend bar charts. Kept distinct from the faint 3-3 grey
// gridlines (darker slate, longer dash, thicker stroke) and given a small
// solid-background label pill so the line and its caption read clearly against
// both the bars and the gridlines, in light and dark mode.

import type { ReactElement } from "react";

export const TYPICAL_LINE_PROPS = {
  stroke: "#64748b", // slate-500 — calm, not the bar colour, darker than gridlines
  strokeWidth: 1.5,
  strokeDasharray: "7 3",
  ifOverflow: "extendDomain" as const,
};

/** A right-aligned pill that sits just above the reference line, showing
 * `text` (e.g. "typical · 5.6 km"). Returns a render function for ReferenceLine's
 * `label`; recharts injects `viewBox`. The pill is sized to the text. */
export function renderTypicalLabel(text: string) {
  return function TypicalLabel({ viewBox }: any): ReactElement | null {
    if (!viewBox) return null;
    const { x, y, width } = viewBox;
    const pillW = Math.max(52, Math.round(text.length * 6.3) + 14);
    const pillH = 17;
    const right = x + width - 2;
    const pillX = right - pillW;
    // Sit above the line, but clamp so it never escapes the top of the plot.
    const pillY = Math.max(y - pillH - 2, 2);
    return (
      <g>
        <rect
          x={pillX}
          y={pillY}
          width={pillW}
          height={pillH}
          rx={4}
          className="fill-white/85 dark:fill-gray-800/85"
        />
        <text
          x={right - 6}
          y={pillY + pillH / 2}
          textAnchor="end"
          dominantBaseline="central"
          fontSize={11}
          fontWeight={600}
          className="fill-slate-600 dark:fill-slate-300"
        >
          {text}
        </text>
      </g>
    );
  };
}
