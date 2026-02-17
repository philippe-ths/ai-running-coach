'use client';

import { useState, useEffect, useCallback } from 'react';
import { CoachReport } from '@/lib/types';
import { Sparkles, ChevronRight, AlertTriangle, HelpCircle, Loader2, RefreshCw } from 'lucide-react';

interface Props {
  activityId: string;
  hasMetrics: boolean;
}

export default function CoachReportPanel({ activityId, hasMetrics }: Props) {
  const [report, setReport] = useState<CoachReport | null>(null);
  const [status, setStatus] = useState<'checking' | 'idle' | 'loading' | 'loaded' | 'error'>('checking');
  const [errorMsg, setErrorMsg] = useState('');

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

  if (!hasMetrics) return null;

  if (status === 'checking') return null;

  if (status === 'idle') {
    return (
      <button
        onClick={() => fetchReport(true)}
        className="w-full bg-white rounded-xl shadow-sm border border-gray-200 p-6 hover:border-blue-300 hover:shadow-md transition-all flex items-center justify-center gap-2 text-gray-600 hover:text-blue-600"
      >
        <Sparkles className="w-5 h-5" />
        <span className="font-medium">Get Coach Analysis</span>
      </button>
    );
  }

  if (status === 'loading') {
    return (
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 flex items-center justify-center gap-2 text-gray-500">
        <Loader2 className="w-5 h-5 animate-spin" />
        <span>Generating coach analysis...</span>
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div className="bg-red-50 rounded-xl border border-red-200 p-6">
        <p className="text-red-700 text-sm">{errorMsg}</p>
        <button
          onClick={() => fetchReport(true)}
          className="mt-2 text-sm text-red-600 hover:text-red-800 underline"
        >
          Try again
        </button>
      </div>
    );
  }

  if (!report) return null;

  const { key_takeaways, next_steps, risks, questions } = report.report;
  const { confidence, generated_at } = report.meta;

  return (
    <div className="space-y-4">
      {/* Key Takeaways */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xl font-semibold text-gray-800 flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-blue-600" />
            Coach Analysis
          </h2>
          <button
            onClick={() => fetchReport(true, true)}
            className="text-xs text-gray-400 hover:text-blue-600 flex items-center gap-1 transition-colors"
            title="Re-run coach analysis"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Re-run
          </button>
        </div>
        <ul className="space-y-2">
          {key_takeaways.map((item, i) => {
            const text = typeof item === 'string' ? item : item.text;
            return (
              <li key={i} className="flex gap-2 text-sm text-gray-700">
                <span className="text-blue-500 mt-0.5 shrink-0">&bull;</span>
                <span>{text}</span>
              </li>
            );
          })}
        </ul>
      </div>

      {/* Next Steps */}
      <div className="space-y-2">
        {next_steps.map((step, i) => (
          <div
            key={i}
            className="bg-green-50 rounded-lg border border-green-200 p-4"
          >
            <div className="flex items-start gap-2">
              <ChevronRight className="w-4 h-4 text-green-600 mt-0.5 shrink-0" />
              <div>
                <p className="font-medium text-green-900 text-sm">{step.action}</p>
                <p className="text-green-800 text-sm mt-1">{step.details}</p>
                <p className="text-green-600 text-xs mt-1 italic">{step.why}</p>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Risks */}
      {risks.length > 0 && (
        <div className="space-y-2">
          {risks.map((risk, i) => (
            <div
              key={i}
              className="bg-amber-50 rounded-lg border border-amber-200 p-4"
            >
              <div className="flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
                <div>
                  <p className="font-medium text-amber-900 text-sm">
                    {risk.flag.replace(/_/g, ' ')}
                  </p>
                  <p className="text-amber-800 text-sm mt-1">{risk.explanation}</p>
                  <p className="text-amber-600 text-xs mt-1 italic">{risk.mitigation}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Questions */}
      {questions.length > 0 && (
        <div className="space-y-2">
          {questions.map((q, i) => (
            <div
              key={i}
              className="bg-blue-50 rounded-lg border border-blue-200 p-4"
            >
              <div className="flex items-start gap-2">
                <HelpCircle className="w-4 h-4 text-blue-600 mt-0.5 shrink-0" />
                <div>
                  <p className="text-blue-900 text-sm font-medium">{q.question}</p>
                  <p className="text-blue-600 text-xs mt-1 italic">{q.reason}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Meta footer */}
      <div className="flex items-center gap-3 text-xs text-gray-400 px-1">
        <span
          className={`inline-flex items-center rounded-full px-2 py-0.5 font-medium ${
            confidence === 'high'
              ? 'bg-green-100 text-green-700'
              : confidence === 'medium'
              ? 'bg-yellow-100 text-yellow-700'
              : 'bg-red-100 text-red-700'
          }`}
        >
          {confidence} confidence
        </span>
        <span>
          {new Date(generated_at).toLocaleDateString(undefined, {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
          })}
        </span>
      </div>

      {/* Debug */}
      <details className="text-xs text-slate-400">
        <summary className="cursor-pointer hover:text-slate-600">
          Debug: LLM Input & Output
        </summary>
        <div className="mt-2 space-y-3">
          <div>
            <h4 className="font-semibold text-slate-500 mb-1">System Prompt</h4>
            <pre className="p-3 bg-slate-50 rounded border border-slate-100 overflow-x-auto whitespace-pre-wrap max-h-64 overflow-y-auto">
              {report.debug.system_prompt}
            </pre>
          </div>
          <div>
            <h4 className="font-semibold text-slate-500 mb-1">Context Pack (LLM Input)</h4>
            <pre className="p-3 bg-slate-50 rounded border border-slate-100 overflow-x-auto max-h-96 overflow-y-auto">
              {JSON.stringify(report.debug.context_pack, null, 2)}
            </pre>
          </div>
          <div>
            <h4 className="font-semibold text-slate-500 mb-1">Raw LLM Response</h4>
            <pre className="p-3 bg-slate-50 rounded border border-slate-100 overflow-x-auto whitespace-pre-wrap max-h-64 overflow-y-auto">
              {report.debug.raw_llm_response || '(empty)'}
            </pre>
          </div>
          <div>
            <h4 className="font-semibold text-slate-500 mb-1">Meta</h4>
            <pre className="p-3 bg-slate-50 rounded border border-slate-100 overflow-x-auto">
              {JSON.stringify(report.meta, null, 2)}
            </pre>
          </div>
        </div>
      </details>
    </div>
  );
}
