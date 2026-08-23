'use client';

// #946: period reports — the coach reviewing a stretch of training the runner
// chooses, across the disciplines the runner chooses. Reuses the exact period +
// discipline vocabulary the Activities filters already speak
// (ActivityTypeFilter / DateRangeFilter) rather than inventing a second picker.

import { useCallback, useEffect, useState } from 'react';
import { format } from 'date-fns';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { Loader2, NotebookPen } from 'lucide-react';
import { fetchFromAPI } from '@/lib/api';
import ActivityTypeFilter from '@/components/trends/ActivityTypeFilter';
import DateRangeFilter, { DateRange } from '@/components/activities/DateRangeFilter';
import type { PeriodReport, PeriodReportListItem } from '@/lib/types';

function toISO(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function daysAgoISO(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return toISO(d);
}

const STATUS_LABEL: Record<string, string> = {
  generating: 'Reviewing…',
  ready: 'Ready',
  failed: "Couldn't write this one",
};

const STATUS_CLASS: Record<string, string> = {
  generating: 'text-gray-500 dark:text-gray-400',
  ready: 'text-emerald-700 dark:text-emerald-400',
  failed: 'text-amber-700 dark:text-amber-400',
};

export default function PeriodReportsPage() {
  const router = useRouter();
  const [types, setTypes] = useState<string[]>([]);
  const [availableTypes, setAvailableTypes] = useState<string[]>([]);
  const [range, setRange] = useState<DateRange>({
    startDate: daysAgoISO(30),
    endDate: null,
  });
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const [reports, setReports] = useState<PeriodReportListItem[] | null>(null);
  const [listError, setListError] = useState(false);

  useEffect(() => {
    fetchFromAPI('/api/trends/types')
      .then((t: string[] | null) => setAvailableTypes(t ?? []))
      .catch(() => {});
  }, []);

  const loadReports = useCallback(() => {
    fetchFromAPI('/api/coach/period-reports')
      .then((r: PeriodReportListItem[] | null) => {
        setReports(r ?? []);
        setListError(false);
      })
      .catch(() => setListError(true));
  }, []);

  useEffect(() => {
    loadReports();
  }, [loadReports]);

  const askForReview = useCallback(async () => {
    if (!range.startDate) {
      setSubmitError('Choose a start date.');
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      const report: PeriodReport = await fetchFromAPI('/api/coach/period-reports', {
        method: 'POST',
        body: JSON.stringify({
          period_start: range.startDate,
          // An open end reads as "through today" everywhere else this filter
          // is used, so a review asked for stays consistent with it.
          period_end: range.endDate ?? toISO(new Date()),
          disciplines: types,
        }),
      });
      router.push(`/period-reports/${report.id}`);
    } catch {
      setSubmitError('Could not ask your coach for a review just now. Try again.');
    } finally {
      setSubmitting(false);
    }
  }, [range, types, router]);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100">Period Reports</h1>
        <p className="text-gray-600 dark:text-gray-400 mt-1">
          A considered review of a stretch of training you choose — not a longer
          write-up of one run.
        </p>
      </header>

      <section className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm p-5 space-y-4">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          Ask for a review
        </h2>
        <div className="flex flex-wrap items-center gap-3">
          <ActivityTypeFilter available={availableTypes} selected={types} onChange={setTypes} />
          <DateRangeFilter
            startDate={range.startDate}
            endDate={range.endDate}
            onChange={setRange}
          />
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => void askForReview()}
            disabled={submitting}
            className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60 disabled:cursor-default transition-colors"
          >
            {submitting ? (
              <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
            ) : (
              <NotebookPen className="w-4 h-4" aria-hidden="true" />
            )}
            {submitting ? 'Asking…' : 'Ask for a review'}
          </button>
          {submitError && (
            <span className="text-sm text-red-600 dark:text-red-400">{submitError}</span>
          )}
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Past reviews</h2>

        {reports === null && !listError && (
          <div className="flex items-center gap-2 text-gray-500 dark:text-gray-400 py-6">
            <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
            <span>Loading…</span>
          </div>
        )}

        {listError && (
          <p className="text-sm text-red-600 dark:text-red-400">Could not load your past reviews.</p>
        )}

        {reports !== null && reports.length === 0 && !listError && (
          <p className="text-gray-500 dark:text-gray-400 italic">
            No reviews yet. Ask for one above.
          </p>
        )}

        {reports !== null && reports.length > 0 && (
          <ul className="space-y-2">
            {reports.map((r) => (
              <li key={r.id}>
                <Link
                  href={`/period-reports/${r.id}`}
                  className="block bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm px-4 py-3 hover:border-blue-300 dark:hover:border-blue-700 transition-colors"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
                        {format(new Date(r.period_start), 'MMM d, yyyy')} –{' '}
                        {format(new Date(r.period_end), 'MMM d, yyyy')}
                        {r.disciplines.length > 0 && (
                          <span className="text-gray-500 dark:text-gray-400 font-normal">
                            {' '}
                            · {r.disciplines.join(', ')}
                          </span>
                        )}
                      </p>
                      {r.headline && (
                        <p className="text-sm text-gray-600 dark:text-gray-400 truncate mt-0.5">
                          {r.headline}
                        </p>
                      )}
                    </div>
                    <span className={`text-xs font-medium whitespace-nowrap ${STATUS_CLASS[r.status] ?? ''}`}>
                      {STATUS_LABEL[r.status] ?? r.status}
                    </span>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
