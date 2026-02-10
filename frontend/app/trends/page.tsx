"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { TrendsData, TrendsRange } from "@/lib/types";
import RangeSelector from "@/components/trends/RangeSelector";
import ActivityTypeFilter from "@/components/trends/ActivityTypeFilter";
import WeeklyDistanceChart from "@/components/trends/WeeklyDistanceChart";
import WeeklyTimeChart from "@/components/trends/WeeklyTimeChart";
import PaceTrendChart from "@/components/trends/PaceTrendChart";

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
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <WeeklyDistanceChart data={data.weekly_distance} />
            <WeeklyTimeChart data={data.weekly_time} />
          </div>
          <PaceTrendChart data={data.pace_trend} />
        </div>
      )}
    </div>
  );
}
