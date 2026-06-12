'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { RefreshCw } from 'lucide-react';
import { fetchFromAPI } from '@/lib/api';

export default function SyncButton() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<null | { upserted: number, analyzed: number, errors: string[] }>(null);

  const handleSync = async () => {
    setLoading(true);
    setStats(null);
    try {
      const data = await fetchFromAPI('/api/sync', { method: 'POST' });
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
        <span className="text-xs text-green-600 dark:text-green-400 font-medium">
          Synced: {stats.upserted} | Analyzed: {stats.analyzed}
          {stats.errors.length > 0 && <span className="text-red-500 ml-1">({stats.errors.length} err)</span>}
        </span>
      )}
      
      <button 
        onClick={handleSync}
        disabled={loading}
        className="flex items-center gap-2 px-3 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-50"
      >
        <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
        {loading ? "Syncing..." : "Sync Now"}
      </button>
    </div>
  );
}
