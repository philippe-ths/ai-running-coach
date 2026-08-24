"use client";

import { Fragment, useEffect, useState, useCallback } from "react";
import { usePublishScreenSelections } from "@/components/coach/CoachSheetContext";
import { TrendsData, TrendsRange, TrendsGranularity } from "@/lib/types";
import { formatDistanceKm, formatDuration } from "@/lib/format";
import { fetchFromAPI } from "@/lib/api";
import RangeSelector from "@/components/trends/RangeSelector";
import WindowStepper from "@/components/trends/WindowStepper";
import GranularitySelector from "@/components/trends/GranularitySelector";
import { resolveGranularity, DAYS_PER_BUCKET, ROLLING_BIN_DAYS } from "@/components/trends/granularity";
import ActivityTypeFilter from "@/components/trends/ActivityTypeFilter";
import TrendBarChart from "@/components/trends/TrendBarChart";
import SufferScoreChart from "@/components/trends/SufferScoreChart";
import EfficiencyTrendChart from "@/components/trends/EfficiencyTrendChart";
import ZoneLoadChart from "@/components/trends/ZoneLoadChart";
import ComparisonRows from "@/components/trends/ComparisonRows";
import MetricSummaryCard from "@/components/trends/MetricSummaryCard";
import { DIR_TEXT, dirFromPct } from "@/components/trends/direction";
import type {
  VolumeMetricName,
  VolumeMetricVsNorm,
  VolumeReport,
} from "@/lib/types/volume";

type WindowMode = "rolling" | "calendar";

// Parse "YYYY-MM-DD" as a local date (avoids the UTC-midnight shift `new
// Date(iso)` would introduce), add `days` (may be negative), and format back.
function addDaysISO(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  dt.setDate(dt.getDate() + days);
  const yy = dt.getFullYear();
  const mm = String(dt.getMonth() + 1).padStart(2, "0");
  const dd = String(dt.getDate()).padStart(2, "0");
  return `${yy}-${mm}-${dd}`;
}

// #746: the efficiency headline compares CLEAN-conditions activities (not hilly,
// stop-heavy or hot) when both windows hold enough of them, because an
// all-activity window mean moves for reasons that are not fitness. Below this many
// on either side the clean mean is too thin to be the steadier read, so the
// all-activity pair is used instead — and either way the basis is stated on screen.
// Which basis to use is a display decision, so this threshold lives here, its only
// reader; the backend computes both means and the counts and picks neither.
const MIN_CLEAN_ACTIVITIES_FOR_COMPARISON = 3;

/**
 * The efficiency card's period-over-period comparison, plus one line naming the
 * basis it is computed on. Which basis is used depends on the data, so it is
 * always stated: an adjustment the runner cannot see is one they cannot reason
 * about, which is the failure #746 is about.
 */
function EfficiencyComparison({
  data,
  prevLabel,
}: {
  data: TrendsData;
  prevLabel?: string;
}) {
  const cur = data.summary;
  const prev = data.previous_summary;

  const cleanCount = cur.efficiency_clean_count ?? 0;
  const prevCleanCount = prev?.efficiency_clean_count ?? 0;
  const totalCount = cur.efficiency_total_count ?? 0;

  // Clean requires BOTH sides: a clean current window compared against an
  // all-conditions previous one would be a worse comparison than either basis
  // alone, not a better one.
  const useClean =
    cur.avg_efficiency_clean_mps_per_bpm != null &&
    prev?.avg_efficiency_clean_mps_per_bpm != null &&
    cleanCount >= MIN_CLEAN_ACTIVITIES_FOR_COMPARISON &&
    prevCleanCount >= MIN_CLEAN_ACTIVITIES_FOR_COMPARISON;

  const current = useClean
    ? cur.avg_efficiency_clean_mps_per_bpm
    : cur.avg_efficiency_mps_per_bpm;
  const previous = useClean
    ? prev?.avg_efficiency_clean_mps_per_bpm
    : prev?.avg_efficiency_mps_per_bpm;

  if (current == null) return null;

  const basis = useClean
    ? `Comparing clean-conditions activities only (${cleanCount} of ${totalCount}) — flat, few stops, not hot.`
    : totalCount > 0
      ? `Comparing all activities: too few clean-conditions ones to compare (${cleanCount} of ${totalCount} here, ${prevCleanCount} in the previous period). Hills, stops and heat are in this number.`
      : null;

  return (
    <>
      {/* Display in meters-per-heartbeat (×60), matching the chart. */}
      <ComparisonRows
        current={current * 60}
        previous={previous != null ? previous * 60 : undefined}
        format={(v) => `${v.toFixed(2)} m/beat`}
        prevLabel={prevLabel}
      />
      {basis && (
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 max-w-md">
          {basis}
        </p>
      )}
    </>
  );
}

export default function TrendsPage() {
  const [range, setRange] = useState<TrendsRange>("30D");
  const [mode, setMode] = useState<WindowMode>("rolling");
  const [granularity, setGranularity] = useState<TrendsGranularity>("day");
  const [selectedTypes, setSelectedTypes] = useState<string[]>([]);
  const [availableTypes, setAvailableTypes] = useState<string[]>([]);
  const [data, setData] = useState<TrendsData | null>(null);
  const [volume, setVolume] = useState<VolumeReport | null>(null);
  const [volumeLoading, setVolumeLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Window navigation (#948): `asOf` judges the (range, mode) window as of this
  // date instead of today; undefined means "today" (the live window). Stepping
  // "previous" pushes the window we're leaving onto `asOfHistory` so "next" can
  // simply pop back to it — a browser-history idiom that sidesteps re-deriving
  // calendar-period math (month/quarter/year lengths vary) on the client.
  const [asOf, setAsOf] = useState<string | undefined>(undefined);
  const [asOfHistory, setAsOfHistory] = useState<(string | undefined)[]>([]);
  const [earliestActivityDate, setEarliestActivityDate] = useState<string | null>(null);

  // The user-chosen bar granularity (#432), clamped to what the range offers.
  // Keeping the raw choice means it's remembered when the runner returns to a
  // range that supports it; the resolver falls back to the range default
  // otherwise (e.g. "month" picked on 3M reverts to "day" on a 7D view).
  const effectiveGranularity = resolveGranularity(range, granularity);

  // Pick the series matching the effective granularity (#432). Typed as any[]
  // because the four series carry granularity-specific point shapes; the charts
  // narrow them via their own props.
  //
  // A series can be undefined when the backend is older than this frontend: the
  // frontend (Vercel) and backend (Railway) deploy independently, so during a
  // rollout the frontend can be live before the backend returns the new
  // `biweekly_*` / `monthly_*` fields. Coalesce to [] so a missing series shows
  // "no data" rather than crashing on `.map` of undefined (#432 follow-up).
  const bySeries = (
    daily?: any[],
    weekly?: any[],
    biweekly?: any[],
    monthly?: any[],
  ): any[] => {
    const series =
      effectiveGranularity === "day"
        ? daily
        : effectiveGranularity === "week"
        ? weekly
        : effectiveGranularity === "2week"
        ? biweekly
        : monthly;
    return series ?? [];
  };

  // Fetch available activity types once on mount
  useEffect(() => {
    fetchFromAPI("/api/trends/types")
      .then((types: string[] | null) => setAvailableTypes(types ?? []))
      .catch(() => {});
  }, []);

  // The window-navigation floor (#948): fetched once, not per step.
  useEffect(() => {
    fetchFromAPI("/api/activities/earliest-date")
      .then((r: { earliest_activity_date: string | null } | null) =>
        setEarliestActivityDate(r?.earliest_activity_date ?? null),
      )
      .catch(() => {});
  }, []);

  // Calendar mode has no meaning for the unbounded "All" range.
  const effectiveMode: WindowMode = range === "ALL" ? "rolling" : mode;

  // A window carries no meaning across a range or framing change (a "previous
  // month" step means nothing once the range becomes 7D), so reset to "today"
  // whenever either changes.
  useEffect(() => {
    setAsOf(undefined);
    setAsOfHistory([]);
  }, [range, effectiveMode]);

  // #767: publish this page's view selections for the coach sheet's screen
  // pointer + ribbon (selections only — the server recomputes the numbers).
  usePublishScreenSelections({
    range,
    types: selectedTypes.length ? selectedTypes : undefined,
  });

  const fetchTrends = useCallback(
    async (r: TrendsRange, types: string[], m: WindowMode, asOfParam?: string) => {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({ range: r, mode: m });
        // Only send types param when the user has explicitly selected a subset
        if (types.length > 0) {
          types.forEach((t) => params.append("types", t));
        }
        if (asOfParam) params.set("as_of", asOfParam);
        const json: TrendsData = await fetchFromAPI(`/api/trends?${params}`);
        setData(json);
      } catch (e: any) {
        setError(e.message || "Failed to load trends");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    fetchTrends(range, selectedTypes, effectiveMode, asOf);
  }, [range, selectedTypes, effectiveMode, asOf, fetchTrends]);

  // The vs-norm comparison shown on the quick-view cards (no norm for "All").
  // Pass the same activity-type filter as the charts (#413) so "typical" is
  // scoped to the selected types and never compares a filtered window against an
  // all-activity norm. Also the source of the window-navigation bounds (#948):
  // both framings' `period_start`/`period_end` ride this same response, so the
  // stepper needs no extra fetch of its own.
  useEffect(() => {
    if (range === "ALL") {
      setVolume(null);
      return;
    }
    let active = true;
    setVolumeLoading(true);
    const params = new URLSearchParams({ range });
    selectedTypes.forEach((t) => params.append("types", t));
    if (asOf) params.set("as_of", asOf);
    fetchFromAPI(`/api/trends/volume?${params}`)
      .then((v: VolumeReport) => active && setVolume(v))
      .catch(() => active && setVolume(null))
      .finally(() => active && setVolumeLoading(false));
    return () => {
      active = false;
    };
  }, [range, selectedTypes, asOf]);

  // Map each metric to its vs-norm comparison for the framing in view.
  const normByMetric: Partial<Record<string, VolumeMetricVsNorm>> = {};
  if (volume && volume.has_baseline) {
    for (const m of volume[effectiveMode].metrics) normByMetric[m.metric] = m;
  }

  // Window navigation (#948): the currently-shown window's bounds, off the same
  // volume fetch every framing already carries `period_start`/`period_end` on
  // regardless of whether a baseline was found.
  const currentFraming = volume ? volume[effectiveMode] : null;
  // Gated on !volumeLoading too: `stepWindowBack`/`stepWindowForward` below
  // close over `currentFraming` from the render at click time, so a second tap
  // landing before the in-flight fetch resolves would otherwise recompute the
  // same target window off the same stale bounds instead of advancing further.
  const canStepBack =
    !volumeLoading &&
    !!currentFraming &&
    !!earliestActivityDate &&
    earliestActivityDate < currentFraming.period_start;
  const canStepForward = !volumeLoading && asOfHistory.length > 0;

  function stepWindowBack() {
    if (!currentFraming) return;
    const newAsOf = addDaysISO(currentFraming.period_start, -1);
    setAsOfHistory((h) => [...h, asOf]);
    setAsOf(newAsOf);
  }

  function stepWindowForward() {
    if (asOfHistory.length === 0) return;
    const prev = asOfHistory[asOfHistory.length - 1];
    setAsOfHistory((h) => h.slice(0, -1));
    setAsOf(prev);
  }

  // "vs prev" labeling (#413): calendar mode names the period it compares to
  // (e.g. "vs last month"), so a month-to-date comparison reads clearly; rolling
  // stays "vs prev".
  const PERIOD_NOUN: Partial<Record<TrendsRange, string>> = {
    "7D": "week",
    "30D": "month",
    "3M": "quarter",
    "6M": "half",
    "1Y": "year",
  };
  const prevLabel =
    effectiveMode === "calendar" && PERIOD_NOUN[range]
      ? `vs last ${PERIOD_NOUN[range]}`
      : "vs prev";

  // Definitions for the key shown under the filters (what "vs typical" and the
  // period-over-period row mean). Built from the live range/mode so the wording
  // stays correct across ranges rather than hardcoding "6 months"/"last month":
  // `baseline_label` already names the norm's history span per range ("the last
  // 6 months" for 30D). "vs typical" is omitted when there's no baseline, since
  // the cards hide that row too.
  const typicalNoun = PERIOD_NOUN[range] ?? "period";
  const typicalHelp = volume?.has_baseline
    ? effectiveMode === "calendar"
      ? `Your typical total for a full ${typicalNoun}, averaged over ${volume.baseline_label}. This ${typicalNoun}'s running total is compared against it, so it climbs toward typical as the ${typicalNoun} progresses.`
      : `Your typical total for a ${typicalNoun}-length window, averaged over ${volume.baseline_label}.`
    : undefined;
  const periodNoun = PERIOD_NOUN[range];
  const prevHelp =
    effectiveMode === "calendar" && periodNoun
      ? `This ${periodNoun} so far against the previous ${periodNoun}'s full total.`
      : "This window against the equal-length window immediately before it.";

  // The runner's typical level per chart bucket (#413), drawn as a reference line.
  // The norm is scaled to the framing's full period length (#436), so
  // norm/window_days is the true per-day rate; each bucket multiplies by its day
  // span (#432: 1/7/14/~30.44), so the line stays coherent with the chosen
  // granularity. `scale` converts norm units to the chart's units (meters→km,
  // seconds→min, effort raw). Undefined hides the line.
  const framing = volume && volume.has_baseline ? volume[effectiveMode] : null;
  const typicalPerBucket = (
    metric: VolumeMetricName,
    scale: number,
  ): number | undefined => {
    const m = normByMetric[metric];
    if (!framing || !m || m.norm == null || framing.window_days <= 0) return undefined;
    const perDay = m.norm / framing.window_days;
    // Rolling buckets are fixed-width blocks (#630: month = 30d), so scale the
    // per-day norm by the rolling bin width; calendar uses the mean month (30.44).
    const bucketDays =
      effectiveMode === "rolling"
        ? ROLLING_BIN_DAYS[effectiveGranularity]
        : DAYS_PER_BUCKET[effectiveGranularity];
    return perDay * bucketDays * scale;
  };

  return (
    <div className="space-y-6">
      <header className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100">Trends</h1>
          <p className="text-gray-600 dark:text-gray-400 mt-1">
            Track your progress over time.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <ActivityTypeFilter
            available={availableTypes}
            selected={selectedTypes}
            onChange={setSelectedTypes}
          />
          <RangeSelector selected={range} onChange={setRange} />
          <GranularitySelector
            range={range}
            selected={effectiveGranularity}
            onChange={setGranularity}
          />
          {range !== "ALL" && (
            <div className="inline-flex rounded-md border border-gray-300 dark:border-gray-600 p-0.5 bg-white dark:bg-gray-800">
              {(["rolling", "calendar"] as WindowMode[]).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={`px-3 py-1.5 text-sm font-medium rounded ${
                    effectiveMode === m
                      ? "bg-blue-600 text-white"
                      : "text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50"
                  }`}
                >
                  {m === "rolling" ? "Rolling" : "Calendar"}
                </button>
              ))}
            </div>
          )}
          {range !== "ALL" && currentFraming && (
            <WindowStepper
              periodStart={currentFraming.period_start}
              periodEnd={currentFraming.period_end}
              canStepBack={canStepBack}
              canStepForward={canStepForward}
              onStepBack={stepWindowBack}
              onStepForward={stepWindowForward}
            />
          )}
        </div>
      </header>

      {/* Key: what the per-card "vs typical" / "vs {period}" comparisons mean.
          Styled as a card matching the metric summary cards below. */}
      {(typicalHelp || prevHelp) && (
        <div className="bg-white dark:bg-gray-800 p-4 rounded-lg border dark:border-gray-700 shadow-sm">
          <div className="text-[11px] font-medium uppercase tracking-wider text-gray-400 dark:text-gray-500">
            Key
          </div>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs">
            {typicalHelp && (
              <>
                <dt className="font-medium text-gray-500 dark:text-gray-400 whitespace-nowrap">
                  vs typical
                </dt>
                <dd className="text-gray-500 dark:text-gray-400">{typicalHelp}</dd>
              </>
            )}
            <dt className="font-medium text-gray-500 dark:text-gray-400 whitespace-nowrap">
              {prevLabel}
            </dt>
            <dd className="text-gray-500 dark:text-gray-400">{prevHelp}</dd>
          </dl>
        </div>
      )}

      {error && (
        <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-md p-4 text-red-700 dark:text-red-300 text-sm">
          {error}
        </div>
      )}

      {loading && !data && (
        <div className="text-gray-400 dark:text-gray-500 text-center py-16">Loading trends...</div>
      )}

      {data && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <MetricSummaryCard
              label="Total Distance"
              value={formatDistanceKm(data.summary.total_distance_m)}
              metric={normByMetric["distance_m"]}
              current={data.summary.total_distance_m}
              previous={data.previous_summary?.total_distance_m}
              format={formatDistanceKm}
              prevLabel={prevLabel}
            />
            <MetricSummaryCard
              label="Total Time"
              value={formatDuration(data.summary.total_moving_time_s)}
              metric={normByMetric["moving_time_s"]}
              current={data.summary.total_moving_time_s}
              previous={data.previous_summary?.total_moving_time_s}
              format={formatDuration}
              prevLabel={prevLabel}
            />
            <MetricSummaryCard
              label="Activities"
              value={data.summary.activity_count.toString()}
              metric={normByMetric["sessions"]}
              current={data.summary.activity_count}
              previous={data.previous_summary?.activity_count}
              format={(v) => Math.round(v).toString()}
              prevLabel={prevLabel}
            />
            <MetricSummaryCard
              label="Total Load"
              value={Math.round(data.summary.total_suffer_score).toLocaleString()}
              metric={normByMetric["effort_score"]}
              current={data.summary.total_suffer_score}
              previous={data.previous_summary?.total_suffer_score}
              format={(v) => Math.round(v).toLocaleString()}
              prevLabel={prevLabel}
            />
          </div>

          <TrendBarChart
            type="distance"
            data={bySeries(
              data.daily_distance,
              data.weekly_distance,
              data.biweekly_distance,
              data.monthly_distance,
            )}
            granularity={effectiveGranularity}
            rolling={effectiveMode === "rolling"}
            typical={typicalPerBucket("distance_m", 1 / 1000)}
            delta={
              <ComparisonRows
                metric={normByMetric["distance_m"]}
                current={data.summary.total_distance_m}
                previous={data.previous_summary?.total_distance_m}
                format={formatDistanceKm}
                prevLabel={prevLabel}
              />
            }
          />
          <TrendBarChart
            type="time"
            data={bySeries(
              data.daily_time,
              data.weekly_time,
              data.biweekly_time,
              data.monthly_time,
            )}
            granularity={effectiveGranularity}
            rolling={effectiveMode === "rolling"}
            typical={typicalPerBucket("moving_time_s", 1 / 60)}
            delta={
              <ComparisonRows
                metric={normByMetric["moving_time_s"]}
                current={data.summary.total_moving_time_s}
                previous={data.previous_summary?.total_moving_time_s}
                format={formatDuration}
                prevLabel={prevLabel}
              />
            }
          />

          <SufferScoreChart
            data={bySeries(
              data.daily_suffer_score,
              data.weekly_suffer_score,
              data.biweekly_suffer_score,
              data.monthly_suffer_score,
            )}
            granularity={effectiveGranularity}
            rolling={effectiveMode === "rolling"}
            typical={typicalPerBucket("effort_score", 1)}
            delta={
              <ComparisonRows
                metric={normByMetric["effort_score"]}
                current={data.summary.total_suffer_score}
                previous={data.previous_summary?.total_suffer_score}
                format={(v) => Math.round(v).toLocaleString()}
                prevLabel={prevLabel}
              />
            }
          />
          {data.efficiency_trend && (
            <EfficiencyTrendChart
              data={data.efficiency_trend}
              delta={<EfficiencyComparison data={data} prevLabel={prevLabel} />}
            />
          )}
          <ZoneLoadChart
            data={bySeries(
              data.daily_zone_load,
              data.weekly_zone_load,
              data.biweekly_zone_load,
              data.monthly_zone_load,
            )}
            granularity={effectiveGranularity}
            rolling={effectiveMode === "rolling"}
            delta={
              <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs tabular-nums">
                {(
                  [
                    ["Easy", data.summary.zone_easy_minutes, data.previous_summary?.zone_easy_minutes],
                    ["Moderate", data.summary.zone_moderate_minutes, data.previous_summary?.zone_moderate_minutes],
                    ["Hard", data.summary.zone_hard_minutes, data.previous_summary?.zone_hard_minutes],
                  ] as const
                ).map(([zone, cur, prev]) => {
                  const c = cur ?? 0;
                  const pct = prev != null && prev > 0 ? Math.round(((c - prev) / prev) * 100) : null;
                  return (
                    <Fragment key={zone}>
                      <span className="text-gray-400 dark:text-gray-500">{zone}</span>
                      <span className="text-gray-400 dark:text-gray-500">
                        {pct !== null ? (
                          <span className={`font-medium ${DIR_TEXT[dirFromPct(pct)]}`}>
                            {pct > 0 ? "+" : ""}
                            {pct}%
                          </span>
                        ) : (
                          "—"
                        )}{" "}
                        · {formatDuration(c * 60)}
                      </span>
                    </Fragment>
                  );
                })}
              </div>
            }
          />
        </div>
      )}
    </div>
  );
}
