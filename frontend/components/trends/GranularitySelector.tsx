"use client";

import { TrendsGranularity, TrendsRange } from "@/lib/types";
import { GRANULARITY_LABEL, GRANULARITY_OPTIONS } from "./granularity";

interface Props {
  range: TrendsRange;
  selected: TrendsGranularity;
  onChange: (granularity: TrendsGranularity) => void;
}

// #432: bar-detail control. Mirrors the Rolling/Calendar toggle's styling. Only
// renders when the range offers more than one granularity (7D is day-only).
export default function GranularitySelector({ range, selected, onChange }: Props) {
  const options = GRANULARITY_OPTIONS[range];
  if (options.length < 2) return null;

  return (
    <div
      className="inline-flex rounded-md border border-gray-300 dark:border-gray-600 p-0.5 bg-white dark:bg-gray-800"
      role="group"
      aria-label="Bar detail"
    >
      {options.map((g) => (
        <button
          key={g}
          onClick={() => onChange(g)}
          className={`px-3 py-1.5 text-sm font-medium rounded ${
            selected === g
              ? "bg-blue-600 text-white"
              : "text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50"
          }`}
        >
          {GRANULARITY_LABEL[g]}
        </button>
      ))}
    </div>
  );
}
