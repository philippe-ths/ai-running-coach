'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import Markdown from 'react-markdown';
import { CoachReport, isMessageReport, isOpenerOnly } from '@/lib/types';
import { Sparkles, ChevronRight, AlertTriangle, HelpCircle, Loader2, RefreshCw } from 'lucide-react';

// While an exchange is in its opener-only state (A4), the fuller turn lands later
// server-side (on the runner's reply or a ~3h timer). The panel re-checks for it
// at this interval, bounded by the attempt cap so it never polls forever.
const OPENER_POLL_INTERVAL_MS = 30000;
const OPENER_POLL_MAX_ATTEMPTS = 40;

// On-demand (re)generation runs in the worker (#260) — the request only enqueues
// it, so the panel polls for the fresh report rather than blocking. A two-stage
// generation can take ~2-6 minutes, and the worker job timeout is 600s (#264), so
// poll every 8s for ~12 minutes — comfortably past the worst-case generation so the
// report is caught when it lands rather than the poll giving up early.
const REGEN_POLL_INTERVAL_MS = 8000;
const REGEN_POLL_MAX_ATTEMPTS = 90;

// Public env var inlined at build time. Set NEXT_PUBLIC_SHOW_DEBUG_PANEL=true
// (or "1") on the build environment to expose the LLM-input/output panel.
const SHOW_DEBUG_PANEL =
  process.env.NEXT_PUBLIC_SHOW_DEBUG_PANEL === 'true' ||
  process.env.NEXT_PUBLIC_SHOW_DEBUG_PANEL === '1';

interface Props {
  activityId: string;
  hasMetrics: boolean;
}

export default function CoachReportPanel({ activityId, hasMetrics }: Props) {
  const [report, setReport] = useState<CoachReport | null>(null);
  const [status, setStatus] = useState<'checking' | 'idle' | 'loading' | 'regenerating' | 'loaded' | 'error'>('checking');
  const [errorMsg, setErrorMsg] = useState('');

  // I1a: tappable RPE/pain input. One answer per question; key by question index.
  // A tap writes a CheckIn (rpe → rpe, pain → pain_score) and the server fires the
  // fuller turn when the exchange is open. Conversational reply/dispute options are
  // out of scope here (they belong to I2 chat-as-continuation).
  const [tapState, setTapState] = useState<Record<number, 'sending' | 'sent' | 'error'>>({});
  const [chosenOption, setChosenOption] = useState<Record<number, string>>({});

  const handleOptionTap = useCallback(
    async (qIndex: number, opt: { id: string; kind: string; payload?: unknown }) => {
      if (opt.kind !== 'rpe' && opt.kind !== 'pain') return;
      const value = Number(opt.payload);
      if (!Number.isFinite(value)) return;
      const field = opt.kind === 'rpe' ? 'rpe' : 'pain_score';
      setChosenOption((prev) => ({ ...prev, [qIndex]: opt.id }));
      setTapState((prev) => ({ ...prev, [qIndex]: 'sending' }));
      try {
        const res = await fetch(`/api/activities/${activityId}/checkin`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ [field]: value }),
        });
        if (!res.ok) throw new Error(`Failed (${res.status})`);
        setTapState((prev) => ({ ...prev, [qIndex]: 'sent' }));
      } catch {
        setTapState((prev) => ({ ...prev, [qIndex]: 'error' }));
      }
    },
    [activityId],
  );

  const fetchReport = useCallback(async (generateIfMissing: boolean, force = false) => {
    setStatus('loading');
    setErrorMsg('');
    try {
      const url = `/api/activities/${activityId}/coach-report?generate=${generateIfMissing}&force=${force}`;
      const res = await fetch(url);
      if (res.status === 404 && !generateIfMissing) {
        setStatus('idle');
        return;
      }
      if (!res.ok) {
        throw new Error(`Failed to load coach report (${res.status})`);
      }
      const data: CoachReport = await res.json();
      setReport(data);
      setStatus('loaded');
    } catch (err: unknown) {
      if (!generateIfMissing) {
        setStatus('idle');
        return;
      }
      setErrorMsg(err instanceof Error ? err.message : 'Something went wrong');
      setStatus('error');
    }
  }, [activityId]);

  // On mount, check if a cached report exists
  useEffect(() => {
    if (!hasMetrics) return;
    fetchReport(false);
  }, [hasMetrics, fetchReport]);

  // A4: while the loaded report is opener-only (the fuller turn has not landed),
  // poll for the fuller turn so it appears without a manual reload. Bounded by an
  // attempt cap that survives re-renders (the ref does not reset), so a fuller
  // that never arrives stops polling rather than looping forever.
  const pollAttempts = useRef(0);
  useEffect(() => {
    if (status !== 'loaded' || !report || !isOpenerOnly(report.report)) return;
    if (pollAttempts.current >= OPENER_POLL_MAX_ATTEMPTS) return;
    const id = setTimeout(async () => {
      pollAttempts.current += 1;
      try {
        const res = await fetch(
          `/api/activities/${activityId}/coach-report?generate=false&force=false`,
        );
        if (res.ok) {
          setReport((await res.json()) as CoachReport);
        }
      } catch {
        // keep waiting; a transient failure should not stop the poll
      }
    }, OPENER_POLL_INTERVAL_MS);
    return () => clearTimeout(id);
  }, [status, report, activityId]);

  // #260: on-demand (re)generation is asynchronous. The "Re-run" / "Get analysis"
  // buttons enqueue a worker job (no gateway timeout) and we poll for the fresh
  // report — the report is "fresh" once its generated_at differs from the one we
  // started with (or, generating from nothing, once any report appears).
  const regenBaseline = useRef<string | null>(null);
  const regenAttempts = useRef(0);
  const [regenTick, setRegenTick] = useState(0);

  const regenerate = useCallback(async () => {
    regenBaseline.current = report?.meta.generated_at ?? null;
    regenAttempts.current = 0;
    setErrorMsg('');
    setStatus('regenerating');
    setRegenTick((t) => t + 1);
    try {
      const res = await fetch(
        `/api/activities/${activityId}/coach-report/regenerate`,
        { method: 'POST' },
      );
      if (!res.ok) throw new Error(`Couldn't start regeneration (${res.status})`);
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Could not start regeneration.');
      setStatus('error');
    }
  }, [activityId, report]);

  useEffect(() => {
    if (status !== 'regenerating') return;
    if (regenAttempts.current >= REGEN_POLL_MAX_ATTEMPTS) {
      setErrorMsg('Your report is still being written — this one is taking a while. It will appear here when it is ready; you can also reload the page in a few minutes.');
      setStatus('error');
      return;
    }
    const id = setTimeout(async () => {
      regenAttempts.current += 1;
      try {
        const res = await fetch(
          `/api/activities/${activityId}/coach-report?generate=false&force=false`,
        );
        if (res.ok) {
          const data = (await res.json()) as CoachReport;
          const ga = data?.meta?.generated_at ?? null;
          if (ga && ga !== regenBaseline.current) {
            setReport(data);
            setStatus('loaded');
            return;
          }
        }
      } catch {
        // transient; keep polling
      }
      setRegenTick((t) => t + 1);
    }, REGEN_POLL_INTERVAL_MS);
    return () => clearTimeout(id);
  }, [status, regenTick, activityId]);

  if (!hasMetrics) return null;

  if (status === 'checking') return null;

  if (status === 'idle') {
    return (
      <button
        onClick={regenerate}
        className="w-full bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6 hover:border-blue-300 dark:hover:border-blue-700 hover:shadow-md transition-all flex items-center justify-center gap-2 text-gray-600 dark:text-gray-400 hover:text-blue-600 dark:hover:text-blue-400"
      >
        <Sparkles className="w-5 h-5" />
        <span className="font-medium">Get Coach Analysis</span>
      </button>
    );
  }

  if (status === 'loading') {
    return (
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6 flex items-center justify-center gap-2 text-gray-500 dark:text-gray-400">
        <Loader2 className="w-5 h-5 animate-spin" />
        <span>Generating coach analysis...</span>
      </div>
    );
  }

  if (status === 'regenerating') {
    return (
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6 flex items-center justify-center gap-2 text-gray-500 dark:text-gray-400">
        <Loader2 className="w-5 h-5 animate-spin" />
        <span>Writing your coach analysis… this can take a minute.</span>
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div className="bg-red-50 dark:bg-red-900/30 rounded-xl border border-red-200 dark:border-red-800 p-6">
        <p className="text-red-700 dark:text-red-300 text-sm">{errorMsg}</p>
        <button
          onClick={regenerate}
          className="mt-2 text-sm text-red-600 dark:text-red-400 hover:text-red-800 dark:hover:text-red-200 underline"
        >
          Try again
        </button>
      </div>
    );
  }

  if (!report) return null;

  const body = report.report;
  // #338: the internal confidence rating is no longer surfaced to the runner
  // (it still flows over the API in report.meta.confidence; display-only change).
  const { generated_at } = report.meta;

  // A3 (ADR 0009): the prose-message shape (schema 2.0) renders the message as
  // markdown with tappable-option chips; the legacy structured shape renders the
  // verdict/takeaway panel below, untouched.
  const reRunButton = (
    <button
      onClick={regenerate}
      className="text-xs text-gray-400 dark:text-gray-500 hover:text-blue-600 dark:hover:text-blue-400 flex items-center gap-1 transition-colors"
      title="Re-run coach analysis"
    >
      <RefreshCw className="w-3.5 h-3.5" />
      Re-run
    </button>
  );

  return (
    <div className="space-y-4">
      {isMessageReport(body) ? (
        <>
          {/* A4: a two-stage Exchange renders as a two-line thread — the opener's
              immediate reaction, then the fuller turn once it lands. A single-shot
              message (no opener_message) renders just the fuller turn, unchanged. */}
          {(() => {
            const hasOpener = Boolean(body.opener_message);
            const hasFuller = (body.message ?? '').trim().length > 0;
            return (
              <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6">
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-xl font-semibold text-gray-800 dark:text-gray-200 flex items-center gap-2">
                    <Sparkles className="w-5 h-5 text-blue-600 dark:text-blue-400" />
                    Coach Analysis
                  </h2>
                  {reRunButton}
                </div>

                {/* Opener turn (stage one) */}
                {hasOpener && (
                  <div className="prose prose-sm prose-gray dark:prose-invert max-w-none prose-p:my-2 prose-ul:my-2 prose-li:my-0.5 prose-headings:mt-3 prose-headings:mb-1.5">
                    <Markdown>{body.opener_message as string}</Markdown>
                  </div>
                )}

                {/* Awaiting the fuller turn (opener-only state) */}
                {hasOpener && !hasFuller && (
                  <p className="mt-3 text-sm text-gray-500 dark:text-gray-400 flex items-center gap-2">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Putting together the full breakdown…
                  </p>
                )}

                {/* Fuller turn (stage two) */}
                {hasFuller && (
                  <>
                    {hasOpener && <hr className="my-4 border-gray-100 dark:border-gray-700" />}
                    {body.headline && (
                      <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-2">{body.headline}</h3>
                    )}
                    <div className="prose prose-sm prose-gray dark:prose-invert max-w-none prose-p:my-2 prose-ul:my-2 prose-li:my-0.5 prose-headings:mt-3 prose-headings:mb-1.5">
                      <Markdown>{body.message}</Markdown>
                    </div>
                  </>
                )}
              </div>
            );
          })()}

          {/* Next steps (tail affordances) — fuller turn only */}
          {(body.message ?? '').trim().length > 0 && body.next_steps.length > 0 && (
            <div className="space-y-2">
              {body.next_steps.map((step, i) => (
                <div key={i} className="bg-green-50 dark:bg-green-900/30 rounded-lg border border-green-200 dark:border-green-800 p-4">
                  <div className="flex items-start gap-2">
                    <ChevronRight className="w-4 h-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                    <div>
                      <p className="font-medium text-green-900 dark:text-green-200 text-sm">{step.action}</p>
                      <p className="text-green-800 dark:text-green-300 text-sm mt-1">{step.details}</p>
                      <p className="text-green-600 dark:text-green-400 text-xs mt-1 italic">{step.why}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Risks */}
          {body.risks.length > 0 && (
            <div className="space-y-2">
              {body.risks.map((risk, i) => (
                <div key={i} className="bg-amber-50 dark:bg-amber-900/30 rounded-lg border border-amber-200 dark:border-amber-800 p-4">
                  <div className="flex items-start gap-2">
                    <AlertTriangle className="w-4 h-4 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
                    <div>
                      <p className="font-medium text-amber-900 dark:text-amber-200 text-sm">{risk.flag.replace(/_/g, ' ')}</p>
                      <p className="text-amber-800 dark:text-amber-300 text-sm mt-1">{risk.explanation}</p>
                      <p className="text-amber-600 dark:text-amber-400 text-xs mt-1 italic">{risk.mitigation}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Questions with tappable-option chips (I1 owns tap delivery) */}
          {body.questions.length > 0 && (
            <div className="space-y-2">
              {body.questions.map((q, i) => (
                <div key={i} className="bg-blue-50 dark:bg-blue-900/30 rounded-lg border border-blue-200 dark:border-blue-800 p-4">
                  <div className="flex items-start gap-2">
                    <HelpCircle className="w-4 h-4 text-blue-600 dark:text-blue-400 mt-0.5 shrink-0" />
                    <div className="flex-1">
                      <p className="text-blue-900 dark:text-blue-200 text-sm font-medium">{q.question}</p>
                      <p className="text-blue-600 dark:text-blue-400 text-xs mt-1 italic">{q.reason}</p>
                      {q.options.length > 0 && (
                        <div className="flex flex-wrap items-center gap-2 mt-2">
                          {q.options.map((opt) => {
                            // I1a delivers RPE/pain taps (the milestone's "quick
                            // RPE/pain options"); other kinds render as before.
                            const interactive = opt.kind === 'rpe' || opt.kind === 'pain';
                            const answered = tapState[i] === 'sent';
                            const sending = tapState[i] === 'sending';
                            const isChosen = chosenOption[i] === opt.id;
                            if (!interactive) {
                              return (
                                <span
                                  key={opt.id}
                                  className="inline-flex items-center rounded-full border border-blue-300 dark:border-blue-700 bg-white dark:bg-gray-800 px-3 py-1 text-xs font-medium text-blue-700 dark:text-blue-300"
                                >
                                  {opt.label}
                                </span>
                              );
                            }
                            return (
                              <button
                                key={opt.id}
                                type="button"
                                disabled={sending || answered}
                                onClick={() => handleOptionTap(i, opt)}
                                className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-medium transition-colors disabled:cursor-default ${
                                  isChosen && answered
                                    ? 'border-green-400 dark:border-green-600 bg-green-100 dark:bg-green-900/40 text-green-800 dark:text-green-200'
                                    : 'border-blue-300 dark:border-blue-700 bg-white dark:bg-gray-800 text-blue-700 dark:text-blue-300 hover:bg-blue-100 dark:hover:bg-blue-900/40 disabled:opacity-50'
                                }`}
                              >
                                {opt.label}
                              </button>
                            );
                          })}
                          {tapState[i] === 'sent' && (
                            <span className="text-xs text-green-700 dark:text-green-300">Thanks — got it</span>
                          )}
                          {tapState[i] === 'error' && (
                            <span className="text-xs text-red-600 dark:text-red-400">Couldn’t save — tap to retry</span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      ) : (
        <>
          {/* Key Takeaways */}
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-xl font-semibold text-gray-800 dark:text-gray-200 flex items-center gap-2">
                <Sparkles className="w-5 h-5 text-blue-600 dark:text-blue-400" />
                Coach Analysis
              </h2>
              {reRunButton}
            </div>
            {body.headline && (
              <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-2">{body.headline}</h3>
            )}
            {body.thesis && (
              <p className="text-sm text-gray-700 dark:text-gray-300 mb-3 leading-relaxed">{body.thesis}</p>
            )}
            {body.lead_argument && (
              <div className="mb-3 bg-blue-50 dark:bg-blue-900/30 rounded-lg border border-blue-200 dark:border-blue-800 p-4">
                <div className="flex items-start gap-2">
                  <Sparkles className="w-4 h-4 text-blue-600 dark:text-blue-400 mt-0.5 shrink-0" />
                  <p className="text-sm font-medium text-blue-900 dark:text-blue-200">{body.lead_argument.text}</p>
                </div>
              </div>
            )}
            <ul className="space-y-2">
              {body.key_takeaways.map((item, i) => {
                const text = typeof item === 'string' ? item : item.text;
                return (
                  <li key={i} className="flex gap-2 text-sm text-gray-700 dark:text-gray-300">
                    <span className="text-blue-500 dark:text-blue-400 mt-0.5 shrink-0">&bull;</span>
                    <span>{text}</span>
                  </li>
                );
              })}
            </ul>
          </div>

          {/* Next Steps */}
          <div className="space-y-2">
            {body.next_steps.map((step, i) => (
              <div key={i} className="bg-green-50 dark:bg-green-900/30 rounded-lg border border-green-200 dark:border-green-800 p-4">
                <div className="flex items-start gap-2">
                  <ChevronRight className="w-4 h-4 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
                  <div>
                    <p className="font-medium text-green-900 dark:text-green-200 text-sm">{step.action}</p>
                    <p className="text-green-800 dark:text-green-300 text-sm mt-1">{step.details}</p>
                    <p className="text-green-600 dark:text-green-400 text-xs mt-1 italic">{step.why}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* Risks */}
          {body.risks.length > 0 && (
            <div className="space-y-2">
              {body.risks.map((risk, i) => (
                <div key={i} className="bg-amber-50 dark:bg-amber-900/30 rounded-lg border border-amber-200 dark:border-amber-800 p-4">
                  <div className="flex items-start gap-2">
                    <AlertTriangle className="w-4 h-4 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
                    <div>
                      <p className="font-medium text-amber-900 dark:text-amber-200 text-sm">
                        {risk.flag.replace(/_/g, ' ')}
                      </p>
                      <p className="text-amber-800 dark:text-amber-300 text-sm mt-1">{risk.explanation}</p>
                      <p className="text-amber-600 dark:text-amber-400 text-xs mt-1 italic">{risk.mitigation}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Questions */}
          {body.questions.length > 0 && (
            <div className="space-y-2">
              {body.questions.map((q, i) => (
                <div key={i} className="bg-blue-50 dark:bg-blue-900/30 rounded-lg border border-blue-200 dark:border-blue-800 p-4">
                  <div className="flex items-start gap-2">
                    <HelpCircle className="w-4 h-4 text-blue-600 dark:text-blue-400 mt-0.5 shrink-0" />
                    <div>
                      <p className="text-blue-900 dark:text-blue-200 text-sm font-medium">{q.question}</p>
                      <p className="text-blue-600 dark:text-blue-400 text-xs mt-1 italic">{q.reason}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* Meta footer */}
      <div className="flex items-center gap-3 text-xs text-gray-400 dark:text-gray-500 px-1">
        <span>
          {new Date(generated_at).toLocaleDateString(undefined, {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
          })}
        </span>
      </div>

      {/* Debug: opt-in via NEXT_PUBLIC_SHOW_DEBUG_PANEL=true (or "1"). Off in
          prod by default; the panel exposes the full context pack and prompt. */}
      {SHOW_DEBUG_PANEL && (
        <details className="text-xs text-slate-400 dark:text-gray-500">
          <summary className="cursor-pointer hover:text-slate-600 dark:hover:text-gray-300">
            Debug: LLM Input & Output
          </summary>
          <div className="mt-2 space-y-3">
            <div>
              <h4 className="font-semibold text-slate-500 dark:text-gray-400 mb-1">System Prompt</h4>
              <pre className="p-3 bg-slate-50 dark:bg-gray-700/50 rounded border border-slate-100 dark:border-gray-700 overflow-x-auto whitespace-pre-wrap max-h-64 overflow-y-auto">
                {report.debug.system_prompt}
              </pre>
            </div>
            <div>
              <h4 className="font-semibold text-slate-500 dark:text-gray-400 mb-1">Context Pack (LLM Input)</h4>
              <pre className="p-3 bg-slate-50 dark:bg-gray-700/50 rounded border border-slate-100 dark:border-gray-700 overflow-x-auto max-h-96 overflow-y-auto">
                {JSON.stringify(report.debug.context_pack, null, 2)}
              </pre>
            </div>
            <div>
              <h4 className="font-semibold text-slate-500 dark:text-gray-400 mb-1">Raw LLM Response</h4>
              <pre className="p-3 bg-slate-50 dark:bg-gray-700/50 rounded border border-slate-100 dark:border-gray-700 overflow-x-auto whitespace-pre-wrap max-h-64 overflow-y-auto">
                {report.debug.raw_llm_response || '(empty)'}
              </pre>
            </div>
            <div>
              <h4 className="font-semibold text-slate-500 dark:text-gray-400 mb-1">Meta</h4>
              <pre className="p-3 bg-slate-50 dark:bg-gray-700/50 rounded border border-slate-100 dark:border-gray-700 overflow-x-auto">
                {JSON.stringify(report.meta, null, 2)}
              </pre>
            </div>
          </div>
        </details>
      )}
    </div>
  );
}
