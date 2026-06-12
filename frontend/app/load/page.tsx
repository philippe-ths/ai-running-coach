"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { LoadData, LoadStatus, LoadWeek } from "@/lib/types";
import { fetchFromAPI } from "@/lib/api";
import { formatDateLabel } from "@/lib/format";
import LoadTrendChart from "@/components/load/LoadTrendChart";

const DAY_LABELS = ["M", "T", "W", "T", "F", "S", "S"];

const STATUS_META: Record<LoadStatus, { label: string; classes: string }> = {
  optimal: {
    label: "Optimal",
    classes: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
  },
  below: {
    label: "Below range",
    classes: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300",
  },
  high: {
    label: "High",
    classes: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300",
  },
  no_baseline: {
    label: "Building baseline",
    classes: "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300",
  },
};

function guidance(week: LoadWeek): string {
  switch (week.status) {
    case "optimal":
      return "Your load this week is in your optimal range to maintain or build fitness.";
    case "below":
      return `Lighter than your recent norm. If you are recovering that is fine; ${Math.round(week.target_min!)}–${Math.round(week.target_max!)} would keep you in range.`;
    case "high":
      return `Heavier than your recent norm. Watch recovery; staying under ${Math.round(week.target_max!)} keeps the risk down.`;
    default:
      return "Not enough history yet to judge this week against your own norm.";
  }
}

function weekRangeLabel(week: LoadWeek): string {
  const start = new Date(week.week_start + "T00:00:00");
  const end = new Date(start);
  end.setDate(end.getDate() + 6);
  const endIso = end.toISOString().slice(0, 10);
  return `${formatDateLabel(week.week_start)} – ${formatDateLabel(endIso)}`;
}

export default function LoadPage() {
  const [data, setData] = useState<LoadData | null>(null);
  const [index, setIndex] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchFromAPI("/api/trends/load")
      .then((d: LoadData | null) => {
        if (!d || !d.weeks.length) {
          setData({ weeks: [] });
        } else {
          setData(d);
          setIndex(d.weeks.length - 1);
        }
      })
      .catch(() => setError("Failed to load training load data."))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="text-gray-500 dark:text-gray-400 py-12 text-center">Loading…</div>;
  }
  if (error || !data) {
    return <div className="text-red-600 dark:text-red-400 py-12 text-center">{error ?? "No data."}</div>;
  }
  if (!data.weeks.length || index === null) {
    return (
      <div className="text-gray-500 dark:text-gray-400 py-12 text-center">
        No activities yet. Sync some runs to see your training load.
      </div>
    );
  }

  const week = data.weeks[index];
  const status = STATUS_META[week.status] ?? STATUS_META.no_baseline;
  const maxDaily = Math.max(...week.daily, 1);
  const isCurrent = index === data.weeks.length - 1;

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Training Load</h1>
        <Link href="/trends" className="text-sm text-blue-600 dark:text-blue-400 hover:underline">
          Trends →
        </Link>
      </header>

      {/* Weekly score card */}
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm p-5">
        <div className="flex items-center justify-between mb-4">
          <button
            type="button"
            aria-label="Previous week"
            onClick={() => setIndex(Math.max(0, index - 1))}
            disabled={index === 0}
            className="p-2 rounded-md text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-30"
          >
            <ChevronLeft size={18} />
          </button>
          <div className="text-sm font-medium text-gray-600 dark:text-gray-400">
            {isCurrent ? "This week" : weekRangeLabel(week)}
          </div>
          <button
            type="button"
            aria-label="Next week"
            onClick={() => setIndex(Math.min(data.weeks.length - 1, index + 1))}
            disabled={isCurrent}
            className="p-2 rounded-md text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-30"
          >
            <ChevronRight size={18} />
          </button>
        </div>

        <div className="flex items-baseline gap-3 mb-2">
          <span className="text-4xl font-bold text-gray-900 dark:text-gray-100">
            {Math.round(week.score)}
          </span>
          <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${status.classes}`}>
            {status.label}
          </span>
          {week.target_min != null && week.target_max != null && (
            <span className="text-xs text-gray-400 dark:text-gray-500">
              target {Math.round(week.target_min)}–{Math.round(week.target_max)}
            </span>
          )}
        </div>
        <p className="text-sm text-gray-600 dark:text-gray-400 mb-6">{guidance(week)}</p>

        {/* Day-of-week breakdown */}
        <div className="flex items-end gap-2 h-20">
          {week.daily.map((score, i) => (
            <div key={i} className="flex-1 flex flex-col items-center gap-1 h-full justify-end">
              <div
                className={`w-full max-w-[28px] rounded-t ${
                  score > 0 ? "bg-blue-500 dark:bg-blue-600" : "bg-gray-100 dark:bg-gray-700"
                }`}
                style={{ height: `${score > 0 ? Math.max((score / maxDaily) * 100, 6) : 4}%` }}
                title={score > 0 ? `${score}` : undefined}
              />
              <span className="text-[10px] text-gray-400 dark:text-gray-500">{DAY_LABELS[i]}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Long-term trend */}
      <LoadTrendChart weeks={data.weeks} />

      {/* Per-activity contributions */}
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm p-5">
        <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3">
          Activities this week
        </h3>
        {week.activities.length === 0 ? (
          <p className="text-sm text-gray-400 dark:text-gray-500">No activities this week.</p>
        ) : (
          <ul className="divide-y divide-gray-200 dark:divide-gray-700">
            {week.activities.map((a) => (
              <li key={a.id}>
                <Link
                  href={`/activity/${a.id}`}
                  className="flex items-center justify-between gap-3 py-3 hover:bg-gray-50 dark:hover:bg-gray-700/50 rounded-md px-2 -mx-2"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
                        {a.name}
                      </span>
                      {a.headline && (
                        <span className="inline-block px-2 py-0.5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-blue-800 dark:text-blue-200 text-[10px] font-medium shrink-0">
                          {a.headline}
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                      {formatDateLabel(a.date)}
                    </div>
                  </div>
                  <span className="text-sm font-mono text-gray-600 dark:text-gray-400 shrink-0">
                    {Math.round(a.effort_score)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
