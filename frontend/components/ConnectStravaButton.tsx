'use client';
import { useEffect, useState } from 'react';
import { CheckCircle2, ExternalLink, RefreshCw } from 'lucide-react';

type StravaStatus = {
  connected: boolean;
  athlete_id: number | null;
  scope: string | null;
  expires_at: number | null;
};

export default function ConnectStravaButton() {
  const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || '';
  const [status, setStatus] = useState<StravaStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE_URL}/api/auth/strava/status`)
      .then((res) => {
        if (!res.ok) throw new Error(`status ${res.status}`);
        return res.json();
      })
      .then((data: StravaStatus) => {
        if (!cancelled) setStatus(data);
      })
      .catch(() => {
        if (!cancelled) setStatus({ connected: false, athlete_id: null, scope: null, expires_at: null });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [API_BASE_URL]);

  if (loading) {
    return <span className="text-sm text-gray-500">Checking Strava…</span>;
  }

  if (status?.connected) {
    return (
      <div className="flex items-center gap-3 whitespace-nowrap">
        <span className="inline-flex items-center gap-2 text-sm text-green-700">
          <CheckCircle2 size={16} />
          <span>
            Connected to Strava
            {status.athlete_id != null && (
              <span className="text-gray-500"> (#{status.athlete_id})</span>
            )}
          </span>
        </span>
        <a
          href={`${API_BASE_URL}/api/auth/strava/login`}
          className="inline-flex items-center gap-1 text-sm text-blue-600 hover:underline"
          title="Re-run the Strava OAuth flow to refresh tokens or switch athletes"
        >
          <RefreshCw size={14} />
          Reconnect
        </a>
      </div>
    );
  }

  return (
    <a
      href={`${API_BASE_URL}/api/auth/strava/login`}
      className="inline-flex items-center gap-2 bg-[#FC4C02] text-white px-4 py-2 rounded font-semibold hover:bg-[#E34402] transition-colors"
    >
      <ExternalLink size={18} />
      Connect with Strava
    </a>
  );
}
