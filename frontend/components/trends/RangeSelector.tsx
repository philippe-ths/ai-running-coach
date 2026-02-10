"use client";

import { TrendsRange } from "@/lib/types";

const RANGES: { key: TrendsRange; label: string }[] = [
  { key: "7D", label: "7D" },
  { key: "30D", label: "30D" },
  { key: "3M", label: "3M" },
  { key: "6M", label: "6M" },
  { key: "1Y", label: "1Y" },
  { key: "ALL", label: "All" },
];

interface Props {
  selected: TrendsRange;
  onChange: (range: TrendsRange) => void;
}

export default function RangeSelector({ selected, onChange }: Props) {
  return (
    <div className="inline-flex rounded-lg border border-gray-200 bg-white p-1 gap-0.5">
      {RANGES.map(({ key, label }) => (
        <button
          key={key}
          onClick={() => onChange(key)}
          className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
            selected === key
              ? "bg-blue-600 text-white shadow-sm"
              : "text-gray-600 hover:bg-gray-100"
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
