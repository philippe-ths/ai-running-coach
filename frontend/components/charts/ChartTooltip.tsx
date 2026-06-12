"use client";

import { ReactNode } from "react";

/**
 * Dark-mode-aware tooltip for the Recharts charts (#247).
 *
 * Recharts' default `<Tooltip>` renders a hard-coded white box with low-contrast
 * text, which is unreadable in dark mode. Passing this as `content` gives every
 * chart the same themed surface. Because `content` replaces the default render,
 * the per-chart `formatter` / `labelFormatter` are passed in here instead of on
 * `<Tooltip>` (where they'd be ignored).
 */

interface TooltipPayloadItem {
  name?: string | number;
  value?: unknown;
  color?: string;
  dataKey?: string | number;
  payload?: Record<string, unknown>;
}

interface ChartTooltipProps {
  // Injected by Recharts when used as `content`.
  active?: boolean;
  payload?: TooltipPayloadItem[];
  label?: string | number;
  /** Mirrors Recharts' formatter: returns [displayValue, displayName]. */
  formatter?: (
    value: unknown,
    name: unknown,
    entry: TooltipPayloadItem
  ) => [ReactNode, ReactNode];
  labelFormatter?: (label: unknown) => ReactNode;
  /** Drop rows whose value is null/undefined (e.g. an empty band series). */
  hideNullRows?: boolean;
  /** Colour each row's name with its series colour (default true). */
  colorNames?: boolean;
}

export default function ChartTooltip({
  active,
  payload,
  label,
  formatter,
  labelFormatter,
  hideNullRows = true,
  colorNames = true,
}: ChartTooltipProps) {
  if (!active || !payload?.length) return null;

  const rows = payload
    .filter((p) => !hideNullRows || (p.value !== null && p.value !== undefined))
    .map((p, i) => {
      let displayName: ReactNode = p.name as ReactNode;
      let displayValue: ReactNode = p.value as ReactNode;
      if (formatter) {
        const [v, n] = formatter(p.value, p.name, p);
        displayValue = v;
        displayName = n;
      }
      return {
        key: `${p.dataKey ?? p.name ?? i}`,
        color: p.color,
        displayName,
        displayValue,
      };
    });

  if (rows.length === 0) return null;

  const displayLabel = labelFormatter ? labelFormatter(label) : label;

  return (
    <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg p-3 text-sm">
      {displayLabel !== undefined && displayLabel !== null && displayLabel !== "" && (
        <p className="font-semibold text-gray-700 dark:text-gray-300 mb-1">
          {displayLabel}
        </p>
      )}
      {rows.map((r) => (
        <div key={r.key} className="flex items-center justify-between gap-6">
          <span
            className="text-gray-600 dark:text-gray-300"
            style={colorNames && r.color ? { color: r.color } : undefined}
          >
            {r.displayName}
          </span>
          <span className="font-medium text-gray-900 dark:text-gray-100">
            {r.displayValue}
          </span>
        </div>
      ))}
    </div>
  );
}
