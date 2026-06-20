"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { TrendsData, TrendsRange } from "@/lib/types";
import { formatDistanceKm, formatDuration } from "@/lib/format";
import { fetchFromAPI } from "@/lib/api";
import RangeSelector from "@/components/trends/RangeSelector";
import ActivityTypeFilter from "@/components/trends/ActivityTypeFilter";
import TrendBarChart from "@/components/trends/TrendBarChart";
import SufferScoreChart from "@/components/trends/SufferScoreChart";
import EfficiencyTrendChart from "@/components/trends/EfficiencyTrendChart";
import ZoneLoadChart from "@/components/trends/ZoneLoadChart";
import MetricDelta from "@/components/trends/MetricDelta";
import StatDiff from "@/components/StatDiff";
import type { VolumeMetricVsNorm, VolumeReport } from "@/lib/types/volume";

type WindowMode = "rolling" | "calendar";

export default function TrendsPage() {
  const [range, setRange] = useState<TrendsRange>("30D");
  const [mode, setMode] = useState<WindowMode>("rolling");
  const [selectedTypes, setSelectedTypes] = useState<string[]>([]);
  const [availableTypes, setAvailableTypes] = useState<string[]>([]);
  const [data, setData] = useState<TrendsData | null>(null);
  const [volume, setVolume] = useState<VolumeReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Derived state for chart granularity
  const isDaily = range === "7D" || range === "30D";
  const granularity = isDaily ? "daily" : "weekly";

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
  useEffect(() => {
    if (range === "ALL") {
      setVolume(null);
      return;
    }
    let active = true;
    fetchFromAPI(`/api/trends/volume?range=${range}`)
      .then((v: VolumeReport) => active && setVolume(v))
      .catch(() => active && setVolume(null));
    return () => {
      active = false;
    };
  }, [range]);

  // Map each metric to its vs-norm comparison for the framing in view.
  const normByMetric: Partial<Record<string, VolumeMetricVsNorm>> = {};
  if (volume && volume.has_baseline) {
    for (const m of volume[effectiveMode].metrics) normByMetric[m.metric] = m;
  }

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
          <Link
            href="/"
            className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700/50"
          >
            Dashboard
          </Link>
        </div>
      </header>

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
            <div className="bg-white dark:bg-gray-800 p-4 rounded-lg border dark:border-gray-700 shadow-sm">
              <div className="text-sm text-gray-500 dark:text-gray-400">Total Distance</div>
              <div className="text-2xl font-bold">
                {formatDistanceKm(data.summary.total_distance_m)}
              </div>
              <MetricDelta
                metric={normByMetric["distance_m"]}
                current={data.summary.total_distance_m}
                previous={data.previous_summary?.total_distance_m}
                format={formatDistanceKm}
              />
            </div>
            <div className="bg-white dark:bg-gray-800 p-4 rounded-lg border dark:border-gray-700 shadow-sm">
              <div className="text-sm text-gray-500 dark:text-gray-400">Total Time</div>
              <div className="text-2xl font-bold">
                {formatDuration(data.summary.total_moving_time_s)}
              </div>
              <MetricDelta
                metric={normByMetric["moving_time_s"]}
                current={data.summary.total_moving_time_s}
                previous={data.previous_summary?.total_moving_time_s}
                format={formatDuration}
              />
            </div>
            <div className="bg-white dark:bg-gray-800 p-4 rounded-lg border dark:border-gray-700 shadow-sm">
              <div className="text-sm text-gray-500 dark:text-gray-400">Activities</div>
              <div className="text-2xl font-bold">
                {data.summary.activity_count}
              </div>
              <MetricDelta
                metric={normByMetric["sessions"]}
                current={data.summary.activity_count}
                previous={data.previous_summary?.activity_count}
                format={(v) => Math.round(v).toString()}
              />
            </div>
            <div className="bg-white dark:bg-gray-800 p-4 rounded-lg border dark:border-gray-700 shadow-sm">
              <div className="text-sm text-gray-500 dark:text-gray-400">Total Load</div>
              <div className="text-2xl font-bold">
                {Math.round(data.summary.total_suffer_score).toLocaleString()}
              </div>
              <MetricDelta
                metric={normByMetric["effort_score"]}
                current={data.summary.total_suffer_score}
                previous={data.previous_summary?.total_suffer_score}
                format={(v) => Math.round(v).toLocaleString()}
              />
            </div>
          </div>

          <TrendBarChart
            type="distance"
            data={isDaily ? data.daily_distance : data.weekly_distance}
            granularity={granularity}
            delta={
              <MetricDelta
                metric={normByMetric["distance_m"]}
                current={data.summary.total_distance_m}
                previous={data.previous_summary?.total_distance_m}
                format={formatDistanceKm}
              />
            }
          />
          <TrendBarChart
            type="time"
            data={isDaily ? data.daily_time : data.weekly_time}
            granularity={granularity}
            delta={
              <MetricDelta
                metric={normByMetric["moving_time_s"]}
                current={data.summary.total_moving_time_s}
                previous={data.previous_summary?.total_moving_time_s}
                format={formatDuration}
              />
            }
          />

          <SufferScoreChart
            data={isDaily ? data.daily_suffer_score : data.weekly_suffer_score}
            granularity={granularity}
            delta={
              <MetricDelta
                metric={normByMetric["effort_score"]}
                current={data.summary.total_suffer_score}
                previous={data.previous_summary?.total_suffer_score}
                format={(v) => Math.round(v).toLocaleString()}
              />
            }
          />
          {data.efficiency_trend && (
            <EfficiencyTrendChart
              data={data.efficiency_trend}
              granularity={granularity}
              delta={
                data.summary.avg_efficiency_mps_per_bpm != null ? (
                  <StatDiff
                    label="vs prev"
                    // Display in meters-per-heartbeat (×60), matching the chart.
                    current={data.summary.avg_efficiency_mps_per_bpm * 60}
                    previous={
                      data.previous_summary?.avg_efficiency_mps_per_bpm != null
                        ? data.previous_summary.avg_efficiency_mps_per_bpm * 60
                        : undefined
                    }
                    format={(v) => `${v.toFixed(2)} m/beat`}
                  />
                ) : undefined
              }
            />
          )}
          <ZoneLoadChart
            data={isDaily ? data.daily_zone_load : data.weekly_zone_load}
            granularity={granularity}
            delta={
              <div>
                <StatDiff
                  label="Easy"
                  current={data.summary.zone_easy_minutes ?? 0}
                  previous={data.previous_summary?.zone_easy_minutes}
                  format={(v) => formatDuration(v * 60)}
                />
                <StatDiff
                  label="Moderate"
                  current={data.summary.zone_moderate_minutes ?? 0}
                  previous={data.previous_summary?.zone_moderate_minutes}
                  format={(v) => formatDuration(v * 60)}
                />
                <StatDiff
                  label="Hard"
                  current={data.summary.zone_hard_minutes ?? 0}
                  previous={data.previous_summary?.zone_hard_minutes}
                  format={(v) => formatDuration(v * 60)}
                />
              </div>
            }
          />
        </div>
      )}
    </div>
  );
}
