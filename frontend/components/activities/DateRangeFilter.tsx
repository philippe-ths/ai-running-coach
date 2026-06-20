"use client";

export interface DateRange {
  startDate: string | null; // YYYY-MM-DD, inclusive
  endDate: string | null; // YYYY-MM-DD, inclusive
}

interface Props {
  startDate: string | null;
  endDate: string | null;
  onChange: (range: DateRange) => void;
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

export default function DateRangeFilter({ startDate, endDate, onChange }: Props) {
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
