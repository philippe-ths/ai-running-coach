"use client";

import { useEffect, useRef, useState } from "react";

interface Props {
  available: string[];
  selected: string[];
  onChange: (selected: string[]) => void;
}

export default function ActivityTypeFilter({
  available,
  selected,
  onChange,
}: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const allSelected = selected.length === 0 || selected.length === available.length;

  function toggle(type: string) {
    if (selected.includes(type)) {
      const next = selected.filter((t) => t !== type);
      onChange(next);
    } else {
      onChange([...selected, type]);
    }
  }

  function selectAll() {
    onChange([]);
  }

  const label = allSelected
    ? "All Activities"
    : selected.length === 1
      ? selected[0]
      : `${selected.length} types`;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md border border-gray-200 bg-white text-gray-700 hover:bg-gray-50 transition-colors"
      >
        <svg
          className="w-4 h-4 text-gray-400"
          fill="none"
          viewBox="0 0 24 24"
          strokeWidth={1.5}
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 3c2.755 0 5.455.232 8.083.678.533.09.917.556.917 1.096v1.044a2.25 2.25 0 0 1-.659 1.591l-5.432 5.432a2.25 2.25 0 0 0-.659 1.591v2.927a2.25 2.25 0 0 1-1.244 2.013L9.75 21v-6.568a2.25 2.25 0 0 0-.659-1.591L3.659 7.409A2.25 2.25 0 0 1 3 5.818V4.774c0-.54.384-1.006.917-1.096A48.32 48.32 0 0 1 12 3Z"
          />
        </svg>
        {label}
        <svg
          className={`w-3.5 h-3.5 text-gray-400 transition-transform ${open ? "rotate-180" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          strokeWidth={2}
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
        </svg>
      </button>

      {open && (
        <div className="absolute right-0 mt-1 w-48 bg-white border border-gray-200 rounded-lg shadow-lg z-20 py-1">
          <button
            onClick={selectAll}
            className={`w-full text-left px-3 py-2 text-sm hover:bg-gray-50 flex items-center gap-2 ${
              allSelected ? "text-blue-600 font-medium" : "text-gray-700"
            }`}
          >
            <span
              className={`w-4 h-4 rounded border flex items-center justify-center text-xs ${
                allSelected
                  ? "bg-blue-600 border-blue-600 text-white"
                  : "border-gray-300"
              }`}
            >
              {allSelected && "✓"}
            </span>
            All Activities
          </button>
          <div className="border-t border-gray-100 my-1" />
          {available.map((type) => {
            const checked = allSelected || selected.includes(type);
            return (
              <button
                key={type}
                onClick={() => toggle(type)}
                className="w-full text-left px-3 py-2 text-sm hover:bg-gray-50 flex items-center gap-2 text-gray-700"
              >
                <span
                  className={`w-4 h-4 rounded border flex items-center justify-center text-xs ${
                    checked
                      ? "bg-blue-600 border-blue-600 text-white"
                      : "border-gray-300"
                  }`}
                >
                  {checked && "✓"}
                </span>
                {type}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
