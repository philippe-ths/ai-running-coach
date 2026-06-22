// #432: bar-granularity (bucket size) options per time range.
//
// The offered granularities are scoped to the range so the control never lets
// the runner pick something nonsensical (a 7-day view as one "month" bar, or a
// 1-year view as ~365 daily bars). Defaults preserve the pre-#432 behaviour:
// 7D/30D showed daily bars, everything longer showed weekly bars.

import type { TrendsGranularity, TrendsRange } from "@/lib/types";

export const GRANULARITY_LABEL: Record<TrendsGranularity, string> = {
  day: "Day",
  week: "Week",
  "2week": "2 weeks",
  month: "Month",
};

// Noun used in chart titles ("Distance per {noun}").
export const BUCKET_NOUN: Record<TrendsGranularity, string> = {
  day: "Day",
  week: "Week",
  "2week": "2 Weeks",
  month: "Month",
};

// Prefix shown before a bucket's date in a chart tooltip.
export const TOOLTIP_PREFIX: Record<TrendsGranularity, string> = {
  day: "",
  week: "Week of ",
  "2week": "Fortnight of ",
  month: "Month of ",
};

// The bucket's first-day key, whichever granularity-specific field carries it.
export function bucketKey(d: {
  period_start?: string;
  week_start?: string;
  date?: string;
}): string {
  return (d.period_start ?? d.week_start ?? d.date) as string;
}

// Approximate days each bucket spans, used to scale the per-day "typical"
// reference line to the chosen bucket. Month uses the mean calendar month.
export const DAYS_PER_BUCKET: Record<TrendsGranularity, number> = {
  day: 1,
  week: 7,
  "2week": 14,
  month: 30.44,
};

// Allowed granularities per range, coarsest-appropriate first column order.
export const GRANULARITY_OPTIONS: Record<TrendsRange, TrendsGranularity[]> = {
  "7D": ["day"],
  "30D": ["day", "week", "2week"],
  "3M": ["week", "2week", "month"],
  "6M": ["week", "2week", "month"],
  "1Y": ["week", "2week", "month"],
  ALL: ["week", "2week", "month"],
};

// Default granularity per range (preserves the pre-#432 daily/weekly split).
export const DEFAULT_GRANULARITY: Record<TrendsRange, TrendsGranularity> = {
  "7D": "day",
  "30D": "day",
  "3M": "week",
  "6M": "week",
  "1Y": "week",
  ALL: "week",
};

// Resolve a desired granularity against a range's allowed set, falling back to
// the range default when the current choice isn't valid for the new range.
export function resolveGranularity(
  range: TrendsRange,
  desired: TrendsGranularity,
): TrendsGranularity {
  return GRANULARITY_OPTIONS[range].includes(desired)
    ? desired
    : DEFAULT_GRANULARITY[range];
}
