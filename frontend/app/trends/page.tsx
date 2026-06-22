"use client";

import { Fragment, useEffect, useState, useCallback } from "react";
import { TrendsData, TrendsRange, TrendsGranularity } from "@/lib/types";
import { formatDistanceKm, formatDuration } from "@/lib/format";
import { fetchFromAPI } from "@/lib/api";
import RangeSelector from "@/components/trends/RangeSelector";
import GranularitySelector from "@/components/trends/GranularitySelector";
import { resolveGranularity, DAYS_PER_BUCKET } from "@/components/trends/granularity";
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

export default function TrendsPage() {
  const [range, setRange] = useState<TrendsRange>("30D");
  const [mode, setMode] = useState<WindowMode>("rolling");
  const [granularity, setGranularity] = useState<TrendsGranularity>("day");
  const [selectedTypes, setSelectedTypes] = useState<string[]>([]);
  const [availableTypes, setAvailableTypes] = useState<string[]>([]);
  const [data, setData] = useState<TrendsData | null>(null);
  const [volume, setVolume] = useState<VolumeReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  // Calendar mode has no meaning for the unbounded "All" range.
  const effectiveMode: WindowMode = range === "ALL" ? "rolling" : mode;

  const fetchTrends = useCallback(
    async (r: TrendsRange, types: string[], m: WindowMode) => {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({ range: r, mode: m });
        // Only send types param when the user has explicitly selected a subset
        if (types.length > 0) {
          types.forEach((t) => params.append("types", t));
        }
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
    fetchTrends(range, selectedTypes, effectiveMode);
  }, [range, selectedTypes, effectiveMode, fetchTrends]);

  // The vs-norm comparison shown on the quick-view cards (no norm for "All").
  // Pass the same activity-type filter as the charts (#413) so "typical" is
  // scoped to the selected types and never compares a filtered window against an
  // all-activity norm.
  useEffect(() => {
    if (range === "ALL") {
      setVolume(null);
      return;
    }
    let active = true;
    const params = new URLSearchParams({ range });
    selectedTypes.forEach((t) => params.append("types", t));
    fetchFromAPI(`/api/trends/volume?${params}`)
      .then((v: VolumeReport) => active && setVolume(v))
      .catch(() => active && setVolume(null));
    return () => {
      active = false;
    };
  }, [range, selectedTypes]);

  // Map each metric to its vs-norm comparison for the framing in view.
  const normByMetric: Partial<Record<string, VolumeMetricVsNorm>> = {};
  if (volume && volume.has_baseline) {
    for (const m of volume[effectiveMode].metrics) normByMetric[m.metric] = m;
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
  const typicalHelp = volume?.has_baseline
    ? `Your average daily rate over ${volume.baseline_label}, projected over the days elapsed in this window.`
    : undefined;
  const periodNoun = PERIOD_NOUN[range];
  const prevHelp =
    effectiveMode === "calendar" && periodNoun
      ? `This ${periodNoun} so far against the previous ${periodNoun}'s full total.`
      : "This window against the equal-length window immediately before it.";

  // The runner's typical level per chart bucket (#413), drawn as a reference line.
  // The #400 norm is scaled to days_elapsed, so norm/days_elapsed is the true
  // per-day rate; each bucket multiplies by its day span (#432: 1/7/14/~30.44),
  // so the line stays coherent with the chosen granularity. `scale` converts norm
  // units to the chart's units (meters→km, seconds→min, effort raw). Undefined
  // hides the line.
  const framing = volume && volume.has_baseline ? volume[effectiveMode] : null;
  const typicalPerBucket = (
    metric: VolumeMetricName,
    scale: number,
  ): number | undefined => {
    const m = normByMetric[metric];
    if (!framing || !m || m.norm == null || framing.days_elapsed <= 0) return undefined;
    const perDay = m.norm / framing.days_elapsed;
    return perDay * DAYS_PER_BUCKET[effectiveGranularity] * scale;
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
              delta={
                data.summary.avg_efficiency_mps_per_bpm != null ? (
                  // Display in meters-per-heartbeat (×60), matching the chart.
                  <ComparisonRows
                    current={data.summary.avg_efficiency_mps_per_bpm * 60}
                    previous={
                      data.previous_summary?.avg_efficiency_mps_per_bpm != null
                        ? data.previous_summary.avg_efficiency_mps_per_bpm * 60
                        : undefined
                    }
                    format={(v) => `${v.toFixed(2)} m/beat`}
                    prevLabel={prevLabel}
                  />
                ) : undefined
              }
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
