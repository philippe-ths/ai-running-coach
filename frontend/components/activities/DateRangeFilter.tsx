"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { formatDateLabel } from "@/lib/format";

export interface DateRange {
  startDate: string | null; // YYYY-MM-DD, inclusive
  endDate: string | null; // YYYY-MM-DD, inclusive
}

interface Props {
  startDate: string | null;
  endDate: string | null;
  onChange: (range: DateRange) => void;
  // The local calendar day of the runner's oldest activity, or null when
  // unknown/no history (#948). Stepping back stops here rather than paging
  // into an empty window.
  earliestActivityDate?: string | null;
}

// Preset windows expressed as days back from today; null = all time.
const PRESETS: { key: string; label: string; days: number | null }[] = [
  { key: "ALL", label: "All", days: null },
  { key: "7D", label: "7D", days: 7 },
  { key: "30D", label: "30D", days: 30 },
  { key: "3M", label: "3M", days: 90 },
  { key: "6M", label: "6M", days: 180 },
  { key: "1Y", label: "1Y", days: 365 },
];

function toISO(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function daysAgoISO(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return toISO(d);
}

function todayISO(): string {
  return toISO(new Date());
}

// Parse "YYYY-MM-DD" as a local date (avoids the UTC-midnight shift `new
// Date(iso)` would introduce), add `days` (may be negative), and format back.
function addDaysISO(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  dt.setDate(dt.getDate() + days);
  return toISO(dt);
}

function daysBetweenInclusiveISO(startIso: string, endIso: string): number {
  const [sy, sm, sd] = startIso.split("-").map(Number);
  const [ey, em, ed] = endIso.split("-").map(Number);
  const start = Date.UTC(sy, sm - 1, sd);
  const end = Date.UTC(ey, em - 1, ed);
  return Math.round((end - start) / 86_400_000) + 1;
}

export default function DateRangeFilter({
  startDate,
  endDate,
  onChange,
  earliestActivityDate,
}: Props) {
  // A preset is "active" when the current range matches the window it would set:
  // a start `days` back and an open end. Editing a date input therefore clears the
  // highlight automatically, since the range no longer matches any preset.
  function applyPreset(days: number | null) {
    if (days === null) onChange({ startDate: null, endDate: null });
    else onChange({ startDate: daysAgoISO(days), endDate: null });
  }

  function isPresetActive(days: number | null): boolean {
    if (days === null) return !startDate && !endDate;
    return startDate === daysAgoISO(days) && !endDate;
  }

  // Stepping (#948): the selected window's own length, moved back/forward by
  // exactly that many days so the arrows always move by "the period already
  // selected" — a preset's window or a manually-picked custom range alike. No
  // meaning for "All" (open on both ends), so the arrows are not offered then.
  const today = todayISO();
  const effectiveEnd = endDate ?? today;
  const windowDays = startDate ? daysBetweenInclusiveISO(startDate, effectiveEnd) : null;

  const canStepBack =
    !!startDate &&
    !!earliestActivityDate &&
    earliestActivityDate < startDate;
  const canStepForward = endDate !== null; // open end already means "through today"

  function stepBack() {
    if (!startDate || windowDays === null) return;
    const newEnd = addDaysISO(startDate, -1);
    const newStart = addDaysISO(newEnd, -(windowDays - 1));
    onChange({ startDate: newStart, endDate: newEnd });
  }

  function stepForward() {
    if (!startDate || windowDays === null) return;
    const newStart = addDaysISO(effectiveEnd, 1);
    const newEnd = addDaysISO(newStart, windowDays - 1);
    // Landing back on (or past) today re-opens the end, matching the preset's
    // own "through today" shape rather than freezing on a stale explicit date.
    if (newEnd >= today) {
      onChange({ startDate: addDaysISO(today, -(windowDays - 1)), endDate: null });
    } else {
      onChange({ startDate: newStart, endDate: newEnd });
    }
  }

  const showStepper = startDate !== null;
  const windowLabel =
    showStepper && startDate
      ? `${formatDateLabel(startDate)} – ${formatDateLabel(effectiveEnd)}`
      : null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="inline-flex rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-1 gap-0.5">
        {PRESETS.map(({ key, label, days }) => (
          <button
            key={key}
            onClick={() => applyPreset(days)}
            className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
              isPresetActive(days)
                ? "bg-blue-600 text-white shadow-sm"
                : "text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {showStepper && (
        <div className="inline-flex items-center gap-1">
          <button
            type="button"
            aria-label="Previous period"
            onClick={stepBack}
            disabled={!canStepBack}
            className="p-2 rounded-md text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-30"
          >
            <ChevronLeft size={18} />
          </button>
          <span className="text-sm font-medium text-gray-600 dark:text-gray-400 min-w-[9.5rem] text-center">
            {windowLabel}
          </span>
          <button
            type="button"
            aria-label="Next period"
            onClick={stepForward}
            disabled={!canStepForward}
            className="p-2 rounded-md text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-30"
          >
            <ChevronRight size={18} />
          </button>
        </div>
      )}

      <div className="inline-flex items-center gap-1.5 text-sm text-gray-600 dark:text-gray-400">
        <input
          type="date"
          aria-label="From date"
          value={startDate ?? ""}
          max={endDate ?? undefined}
          onChange={(e) => onChange({ startDate: e.target.value || null, endDate })}
          className="rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1.5 text-gray-700 dark:text-gray-300"
        />
        <span aria-hidden="true" className="text-gray-400 dark:text-gray-500">
          –
        </span>
        <input
          type="date"
          aria-label="To date"
          value={endDate ?? ""}
          min={startDate ?? undefined}
          onChange={(e) => onChange({ startDate, endDate: e.target.value || null })}
          className="rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1.5 text-gray-700 dark:text-gray-300"
        />
      </div>
    </div>
  );
}
