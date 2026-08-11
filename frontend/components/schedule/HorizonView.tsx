"use client";

// #830: the horizon's shell — the two filters, the fetch, and the states the
// chart itself should never have to know about.
//
// Both filters sit in ONE row ABOVE the chart card rather than inside it: a
// control that scopes what is drawn belongs outside the drawing. Range changes
// refetch (the window is a server parameter); segmentation does not (it is a
// view over data already in hand), so switching Intent/Discipline/Both never
// costs a request.
//
// A refetch holds the previous chart at reduced opacity instead of dropping to
// a skeleton, so changing range does not make the page jump.

import { useCallback, useEffect, useState } from "react";
import { fetchFromAPI } from "@/lib/api";
import type { ScheduleHorizon } from "@/lib/types/schedule";
import HorizonChart, { type Segmentation } from "./HorizonChart";

const RANGES = [
  { label: "1M", weeks: 4, name: "One month" },
  { label: "2M", weeks: 8, name: "Two months" },
  { label: "3M", weeks: 12, name: "Three months" },
] as const;

const SEGMENTATIONS: { value: Segmentation; label: string }[] = [
  { value: "intent", label: "Intent" },
  { value: "discipline", label: "Discipline" },
  { value: "both", label: "Both" },
];

function SegmentedControl<T extends string | number>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: T; label: string; name?: string }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="inline-flex rounded-md border border-gray-200 bg-white p-0.5 dark:border-gray-700 dark:bg-gray-800"
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={String(option.value)}
            type="button"
            aria-pressed={selected}
            onClick={() => onChange(option.value)}
            className={`rounded px-2.5 py-1 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${
              selected
                ? "bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900"
                : "text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
            }`}
          >
            <span aria-hidden={option.name ? "true" : undefined}>{option.label}</span>
            {option.name && <span className="sr-only">{option.name}</span>}
          </button>
        );
      })}
    </div>
  );
}

export default function HorizonView({
  refreshToken = 0,
}: {
  /** Bumped by the page when the runner's goal races change. The race marker
   *  rides this payload, so a new race means a refetch, not a redraw. */
  refreshToken?: number;
}) {
  const [weeks, setWeeks] = useState<number>(12);
  const [segmentation, setSegmentation] = useState<Segmentation>("both");
  const [showTable, setShowTable] = useState(false);
  const [horizon, setHorizon] = useState<ScheduleHorizon | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // `refreshToken` is a dependency of the CALLBACK rather than of the effect, so
  // a race change re-identifies `load` and the one effect below refetches. It is
  // deliberately unread inside: its only job is to say "the server has something
  // new for you".
  const load = useCallback(async (count: number) => {
    setLoading(true);
    setError(null);
    try {
      const data: ScheduleHorizon | null = await fetchFromAPI(
        `/api/schedule/horizon?weeks=${count}`,
      );
      if (data) setHorizon(data);
      else setError("Your horizon is not available right now.");
    } catch {
      setError("Could not load your horizon.");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshToken]);

  useEffect(() => {
    load(weeks);
  }, [weeks, load]);

  // Empty is "no week carries any load", not "no plan": a plan whose weeks are
  // all shape-less would otherwise draw twelve blank rows and a scale of zero.
  const isEmpty =
    !!horizon && !horizon.weeks.some((w) => (w.effort_score ?? 0) > 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <SegmentedControl
          label="Horizon range"
          options={RANGES.map((r) => ({ value: r.weeks, label: r.label, name: r.name }))}
          value={weeks}
          onChange={setWeeks}
        />
        <SegmentedControl
          label="Split the bars by"
          options={SEGMENTATIONS}
          value={segmentation}
          onChange={setSegmentation}
        />
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-300">
          {error}
        </div>
      )}

      {loading && !horizon && (
        <div className="py-16 text-center text-gray-400 dark:text-gray-500">
          Loading your horizon…
        </div>
      )}

      {horizon && (
        <section
          className={`rounded-lg border border-gray-200 bg-white p-4 shadow-sm transition-opacity dark:border-gray-700 dark:bg-gray-800 sm:p-5 ${
            loading ? "opacity-60" : ""
          }`}
        >
          <h2 className="mb-1 text-sm font-semibold text-gray-700 dark:text-gray-300">
            Load per week
          </h2>
          <p className="mb-4 text-xs text-gray-500 dark:text-gray-400">
            {horizon.has_plan
              ? "The shape of the block: concrete for the next few weeks, sketched beyond."
              : "You have no plan yet, so this is the shape of the weeks ahead as it stands."}
          </p>

          {isEmpty ? (
            <p className="py-8 text-center text-sm text-gray-400 dark:text-gray-500">
              Nothing is planned across these weeks yet.
            </p>
          ) : (
            <>
              <HorizonChart
                horizon={horizon}
                segmentation={segmentation}
                showTable={showTable}
              />
              <div className="mt-4 border-t border-gray-100 pt-3 dark:border-gray-700">
                {/* The table is now DRAWN on demand rather than kept sr-only:
                    the chart's rows became labelled buttons (#830, tap to
                    expand), so a permanently hidden twin would have a screen
                    reader read the whole horizon twice. It is a genuine
                    disclosure now, so it carries aria-expanded. */}
                <button
                  type="button"
                  aria-expanded={showTable}
                  aria-controls="horizon-numbers"
                  onClick={() => setShowTable((v) => !v)}
                  className="-ml-2 rounded-md px-2 py-1 text-xs font-medium text-blue-700 hover:bg-blue-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:text-blue-400 dark:hover:bg-blue-900/30"
                >
                  {showTable ? "Hide the numbers" : "Show the numbers"}
                </button>
              </div>
            </>
          )}
        </section>
      )}
    </div>
  );
}
