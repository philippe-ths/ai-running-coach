"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { TrendsData, TrendsRange } from "@/lib/types";
import { formatDistanceKm, formatDuration } from "@/lib/format";
import RangeSelector from "@/components/trends/RangeSelector";
import ActivityTypeFilter from "@/components/trends/ActivityTypeFilter";
import WeeklyDistanceChart from "@/components/trends/WeeklyDistanceChart";
import WeeklyTimeChart from "@/components/trends/WeeklyTimeChart";
import PaceTrendChart from "@/components/trends/PaceTrendChart";
import SufferScoreChart from "@/components/trends/SufferScoreChart";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

export default function TrendsPage() {
  const [range, setRange] = useState<TrendsRange>("30D");
  const [selectedTypes, setSelectedTypes] = useState<string[]>([]);
  const [availableTypes, setAvailableTypes] = useState<string[]>([]);
  const [data, setData] = useState<TrendsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch available activity types once on mount
  useEffect(() => {
    fetch(`${API_BASE_URL}/api/trends/types`)
      .then((res) => (res.ok ? res.json() : []))
      .then((types: string[]) => setAvailableTypes(types))
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
      const res = await fetch(`${API_BASE_URL}/api/trends?${params}`);
      if (!res.ok) throw new Error(`API error: ${res.statusText}`);
      const json: TrendsData = await res.json();
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
          <h1 className="text-3xl font-bold text-gray-900">Trends</h1>
          <p className="text-gray-600 mt-1">
            Track your progress over time.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <ActivityTypeFilter
            available={availableTypes}
            selected={selectedTypes}
            onChange={setSelectedTypes}
          />
          <RangeSelector selected={range} onChange={setRange} />
          <Link
            href="/"
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
          >
            Dashboard
          </Link>
        </div>
      </header>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-md p-4 text-red-700 text-sm">
          {error}
        </div>
      )}

      {loading && !data && (
        <div className="text-gray-400 text-center py-16">Loading trends...</div>
      )}

      {data && (
        <div className="space-y-6">
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-white p-4 rounded-lg border shadow-sm">
              <div className="text-sm text-gray-500">Total Distance</div>
              <div className="text-2xl font-bold">{formatDistanceKm(data.summary.total_distance_m)}</div>
            </div>
            <div className="bg-white p-4 rounded-lg border shadow-sm">
              <div className="text-sm text-gray-500">Total Time</div>
              <div className="text-2xl font-bold">{formatDuration(data.summary.total_moving_time_s)}</div>
            </div>
            <div className="bg-white p-4 rounded-lg border shadow-sm">
              <div className="text-sm text-gray-500">Activities</div>
              <div className="text-2xl font-bold">{data.summary.activity_count}</div>
            </div>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <WeeklyDistanceChart
              data={range === "7D" || range === "30D" ? data.daily_distance : data.weekly_distance}
              granularity={range === "7D" || range === "30D" ? "daily" : "weekly"}
            />
            <WeeklyTimeChart
              data={range === "7D" || range === "30D" ? data.daily_time : data.weekly_time}
              granularity={range === "7D" || range === "30D" ? "daily" : "weekly"}
            />
          </div>
          <PaceTrendChart data={data.pace_trend} />
          <SufferScoreChart
            data={range === "7D" || range === "30D" ? data.daily_suffer_score : data.suffer_score}
            granularity={range === "7D" || range === "30D" ? "daily" : "per-activity"}
          />
        </div>
      )}
    </div>
  );
}
