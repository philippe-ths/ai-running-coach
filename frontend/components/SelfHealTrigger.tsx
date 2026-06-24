'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { RotateCw } from 'lucide-react';
import { fetchFromAPI } from '@/lib/api';

/**
 * User-triggered self-healing for missed Strava webhooks (#123, ADR 0006).
 *
 * Replaces the deleted time-based polling fallback: it fires a cheap, bounded
 * check (one Strava call on the worker) when the app opens AND on an explicit
 * Refresh tap. The backend bound collapses rapid taps to one check, so this is
 * safe to fire on every mount. After a short beat we refresh the server
 * components so any just-processed activity shows up without a manual reload.
 */
export default function SelfHealTrigger() {
  const router = useRouter();
  const [checking, setChecking] = useState(false);

  const runCheck = useCallback(async () => {
    setChecking(true);
    try {
      await fetchFromAPI('/api/activities/refresh', { method: 'POST' });
      // The check runs on the worker; give it a beat, then pull any new activity.
      setTimeout(() => router.refresh(), 4000);
    } catch (err) {
      // Best-effort safety net — never surface an error to the user.
      console.error('Self-heal check failed', err);
    } finally {
      setTimeout(() => setChecking(false), 4000);
    }
  }, [router]);

  // Fire once on app-open.
  useEffect(() => {
    runCheck();
  }, [runCheck]);

  return (
    <button
      onClick={runCheck}
      disabled={checking}
      title="Check Strava for runs your coach may have missed"
      className="flex items-center gap-2 px-3 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-50"
    >
      <RotateCw size={14} className={checking ? 'animate-spin' : ''} />
      {checking ? 'Checking...' : 'Refresh'}
    </button>
  );
}
