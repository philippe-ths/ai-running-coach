'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { RefreshCw } from 'lucide-react';

export default function SyncButton() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<null | { upserted: number, analyzed: number, errors: string[] }>(null);

  const handleSync = async () => {
    setLoading(true);
    setStats(null);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'}/api/sync`, {
        method: 'POST'
      });
      
      if (!res.ok) throw new Error("Sync failed");
      
      const data = await res.json();
      setStats(data);
      router.refresh(); // Reload server components to show new activities
      
      // Auto-hide stats after 5s
      setTimeout(() => setStats(null), 5000);

    } catch (err) {
      console.error(err);
      alert("Failed to sync with Strava.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex items-center gap-2">
      {stats && (
        <span className="text-xs text-green-600 font-medium">
          Synced: {stats.upserted} | Analyzed: {stats.analyzed}
          {stats.errors.length > 0 && <span className="text-red-500 ml-1">({stats.errors.length} err)</span>}
        </span>
      )}
      
      <button 
        onClick={handleSync}
        disabled={loading}
        className="flex items-center gap-2 px-3 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50"
      >
        <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
        {loading ? "Syncing..." : "Sync Now"}
      </button>
    </div>
  );
}
