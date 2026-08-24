'use client';

// #946: one period report — the status poll while it's being written, and the
// finished review once it's ready. The prose treatment mirrors
// `CoachReportPanel` (react-markdown + remark-gfm) so a period report reads
// like a report, not a different kind of page.

import { useEffect, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { format } from 'date-fns';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ArrowLeft, Loader2, NotebookPen } from 'lucide-react';
import { fetchFromAPI } from '@/lib/api';
import type { PeriodReport } from '@/lib/types';

const POLL_INTERVAL_MS = 3000;
const MAX_POLLS = 80; // ~4 minutes; a stronger model over a wider pack than one run

export default function PeriodReportDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const [report, setReport] = useState<PeriodReport | null>(null);
  const [error, setError] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const polls = useRef(0);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;

    async function poll() {
      try {
        const data: PeriodReport | null = await fetchFromAPI(`/api/coach/period-reports/${id}`);
        if (cancelled) return;
        setReport(data);
        setError(data === null);
        if (data?.status === 'generating' && polls.current < MAX_POLLS) {
          polls.current += 1;
          timer.current = setTimeout(poll, POLL_INTERVAL_MS);
        }
      } catch {
        if (!cancelled) setError(true);
      }
    }

    void poll();
    return () => {
      cancelled = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [id]);

  return (
    <div className="space-y-6">
      <Link
        href="/period-reports"
        className="inline-flex items-center gap-1.5 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 transition-colors"
      >
        <ArrowLeft className="w-4 h-4" aria-hidden="true" />
        All period reports
      </Link>

      {error && !report && (
        <div className="bg-red-50 dark:bg-red-900/30 rounded-xl border border-red-200 dark:border-red-800 p-6">
          <p className="text-red-700 dark:text-red-300 text-sm">
            This period report could not be found.
          </p>
        </div>
      )}

      {!error && !report && (
        <div className="flex items-center justify-center gap-2 text-gray-500 dark:text-gray-400 py-10">
          <Loader2 className="w-5 h-5 animate-spin" aria-hidden="true" />
          <span>Loading…</span>
        </div>
      )}

      {report && (
        <>
          <header className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm p-5">
            <div className="flex items-start gap-3">
              <div className="rounded-full bg-blue-50 dark:bg-blue-900/30 p-2">
                <NotebookPen className="w-5 h-5 text-blue-600 dark:text-blue-400" aria-hidden="true" />
              </div>
              <div>
                <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100">
                  {format(new Date(report.period_start), 'MMM d, yyyy')} –{' '}
                  {format(new Date(report.period_end), 'MMM d, yyyy')}
                </h1>
                <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
                  {report.disciplines.length > 0 ? report.disciplines.join(', ') : 'Every discipline'}
                </p>
              </div>
            </div>
          </header>

          {report.status === 'generating' && (
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm p-6 flex items-center gap-3">
              <Loader2 className="w-5 h-5 animate-spin text-blue-600 dark:text-blue-400" aria-hidden="true" />
              <p className="text-sm text-gray-600 dark:text-gray-400">{report.message}</p>
            </div>
          )}

          {report.status === 'failed' && (
            <div className="bg-amber-50 dark:bg-amber-900/20 rounded-lg border border-amber-200 dark:border-amber-800 p-6">
              <p className="text-sm text-amber-800 dark:text-amber-300">{report.message}</p>
              <Link
                href="/period-reports"
                className="inline-block mt-3 text-sm text-amber-700 dark:text-amber-400 underline hover:no-underline"
              >
                Ask for another review
              </Link>
            </div>
          )}

          {report.status === 'ready' && report.report && (
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm p-6 space-y-4">
              {report.report.headline && (
                <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                  {report.report.headline}
                </h2>
              )}
              <div className="prose prose-sm prose-gray dark:prose-invert max-w-none prose-p:my-2 prose-ul:my-2 prose-li:my-0.5 prose-headings:mt-3 prose-headings:mb-1.5">
                <Markdown remarkPlugins={[remarkGfm]}>{report.report.message}</Markdown>
              </div>
              {report.report.next_steps.length > 0 && (
                <div className="pt-2 border-t border-gray-100 dark:border-gray-700">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-2">
                    Carrying forward
                  </h3>
                  <ul className="space-y-1">
                    {report.report.next_steps.map((step, i) => (
                      <li key={i} className="text-sm text-gray-700 dark:text-gray-300 flex gap-2">
                        <span className="text-gray-400 dark:text-gray-500" aria-hidden="true">
                          &bull;
                        </span>
                        {step}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
