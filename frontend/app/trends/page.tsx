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

const DiffStat = ({
  current,
  previous,
  format,
}: {
  current: number;
  previous?: number | null;
  format: (val: number) => string;
}) => {
  if (previous === undefined || previous === null) return null;
  const diff = current - previous;
  // Use a small epsilon for float comparison, or checking if effectively 0
  if (Math.abs(diff) < 0.001) {
    return <div className="text-xs text-gray-400 dark:text-gray-500 mt-1">No change</div>;
  }

  const isPositive = diff > 0;
  // For these metrics (distance, time, count, score), more is generally "active" (green).
  // If we had "pace" where lower is better, we'd need a prop logic.
  const color = isPositive ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400";
  const arrow = isPositive ? "↑" : "↓";

  return (
    <div className={`text-xs ${color} mt-1 font-medium`}>
      {arrow} {format(Math.abs(diff))}
    </div>
  );
};

export default function TrendsPage() {
  const [range, setRange] = useState<TrendsRange>("30D");
  const [selectedTypes, setSelectedTypes] = useState<string[]>([]);
  const [availableTypes, setAvailableTypes] = useState<string[]>([]);
  const [data, setData] = useState<TrendsData | null>(null);
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

  const fetchTrends = useCallback(async (r: TrendsRange, types: string[]) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ range: r });
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
  }, []);

  useEffect(() => {
    fetchTrends(range, selectedTypes);
  }, [range, selectedTypes, fetchTrends]);

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
              <DiffStat
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
              <DiffStat
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
              <DiffStat
                current={data.summary.activity_count}
                previous={data.previous_summary?.activity_count}
                format={(v) => v.toString()}
              />
            </div>
            <div className="bg-white dark:bg-gray-800 p-4 rounded-lg border dark:border-gray-700 shadow-sm">
              <div className="text-sm text-gray-500 dark:text-gray-400">Total Load</div>
              <div className="text-2xl font-bold">
                {Math.round(data.summary.total_suffer_score).toLocaleString()}
              </div>
              <DiffStat
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
          />
          <TrendBarChart
            type="time"
            data={isDaily ? data.daily_time : data.weekly_time}
            granularity={granularity}
          />

          <SufferScoreChart
            data={isDaily ? data.daily_suffer_score : data.weekly_suffer_score}
            granularity={granularity}
          />
          {data.efficiency_trend && (
            <EfficiencyTrendChart
              data={data.efficiency_trend}
              granularity={granularity}
            />
          )}
          <ZoneLoadChart
            data={isDaily ? data.daily_zone_load : data.weekly_zone_load}
            granularity={granularity}
          />
        </div>
      )}
    </div>
  );
}
