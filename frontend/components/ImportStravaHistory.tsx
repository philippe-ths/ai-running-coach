'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { History, Loader2, CheckCircle2, AlertTriangle } from 'lucide-react';
import { fetchFromAPI } from '@/lib/api';

type ImportStatus = {
  id: string;
  since_date: string;
  status: 'running' | 'completed' | 'failed';
  activities_imported: number;
  error: string | null;
  created_at: string;
  updated_at: string;
};

const POLL_INTERVAL_MS = 3000;

function defaultSinceDate(): string {
  // Default to one year back — a sensible starting window the user can widen.
  const d = new Date();
  d.setFullYear(d.getFullYear() - 1);
  return d.toISOString().slice(0, 10);
}

export default function ImportStravaHistory() {
  const router = useRouter();
  const [sinceDate, setSinceDate] = useState<string>(defaultSinceDate());
  const [importState, setImportState] = useState<ImportStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const today = new Date().toISOString().slice(0, 10);
  const running = importState?.status === 'running';

  const poll = useCallback(async () => {
    try {
      const data: ImportStatus | null = await fetchFromAPI('/api/strava/import/status');
      setImportState(data);
      if (data && data.status === 'running') {
        timer.current = setTimeout(poll, POLL_INTERVAL_MS);
      } else if (data && data.status === 'completed') {
        // New activities landed — refresh server components (dashboard/trends).
        router.refresh();
      }
    } catch (err) {
      console.error(err);
    }
  }, [router]);

  // On mount, surface any in-flight or recent import and resume polling.
  useEffect(() => {
    poll();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [poll]);

  const handleImport = async () => {
    if (!sinceDate) return;
    setStarting(true);
    try {
      const data: ImportStatus = await fetchFromAPI('/api/strava/import', {
        method: 'POST',
        body: JSON.stringify({ since_date: sinceDate }),
      });
      setImportState(data);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(poll, POLL_INTERVAL_MS);
    } catch (err) {
      console.error(err);
      alert('Failed to start Strava history import. Is Strava connected?');
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="bg-white dark:bg-gray-800 p-6 border dark:border-gray-700 rounded-xl shadow-sm space-y-4">
      <div className="flex items-center gap-2">
        <History size={18} className="text-blue-600 dark:text-blue-400" />
        <h2 className="text-lg font-semibold">Import Strava History</h2>
      </div>
      <p className="text-sm text-gray-500 dark:text-gray-400">
        Back-fill your past Strava activities so trends and load have history to
        work with. Imported runs are stored as data only — your coach won&apos;t
        analyse or message you about old activities.
      </p>

      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label
            htmlFor="import-since"
            className="block text-sm font-medium mb-1"
          >
            Import from
          </label>
          <input
            id="import-since"
            type="date"
            value={sinceDate}
            max={today}
            disabled={running}
            onChange={(e) => setSinceDate(e.target.value)}
            className="border dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100 rounded p-2 disabled:opacity-50"
          />
        </div>
        <button
          onClick={handleImport}
          disabled={starting || running || !sinceDate}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
        >
          {starting || running ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <History size={16} />
          )}
          {running ? 'Importing…' : starting ? 'Starting…' : 'Import history'}
        </button>
      </div>

      {importState && (
        <div className="text-sm">
          {importState.status === 'running' && (
            <p className="flex items-center gap-2 text-gray-600 dark:text-gray-300">
              <Loader2 size={14} className="animate-spin" />
              Importing… {importState.activities_imported} activities so far.
            </p>
          )}
          {importState.status === 'completed' && (
            <p className="flex items-center gap-2 text-green-600 dark:text-green-400">
              <CheckCircle2 size={14} />
              Imported {importState.activities_imported} activities.
            </p>
          )}
          {importState.status === 'failed' && (
            <p className="flex items-center gap-2 text-red-600 dark:text-red-400">
              <AlertTriangle size={14} />
              Import failed{importState.error ? `: ${importState.error}` : '.'}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
