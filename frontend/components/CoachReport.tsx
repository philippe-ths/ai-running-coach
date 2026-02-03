import React from 'react';
import { Advice, CoachReportData } from '@/lib/types';
import Markdown from 'react-markdown';
import { 
  CheckCircle2, 
  AlertTriangle, 
  TrendingUp, 
  Target, 
  ArrowRight,
  Zap,
  Activity,
  Calendar
} from 'lucide-react';

interface CoachReportProps {
  advice: Advice;
}

export default function CoachReport({ advice }: CoachReportProps) {
  const report = advice.structured_report;

  // Fallback to legacy Markdown if no structured report is available
  if (!report) {
    return (
      <div className="prose prose-blue max-w-none text-gray-700">
        <Markdown>{advice.full_text}</Markdown>
      </div>
    );
  }

  // --- Structured Render ---
  return (
    <div className="space-y-6">
      
      {/* 1. Headline & Verdict */}
      <div className="border-b border-gray-100 pb-4">
        <div className="flex items-center gap-2 mb-2">
            <span className="bg-blue-100 text-blue-800 text-xs font-semibold px-2.5 py-0.5 rounded capitalize">
                {report.session_type}
            </span>
            {report.warnings.length > 0 && (
                <span className="bg-red-100 text-red-800 text-xs font-semibold px-2.5 py-0.5 rounded flex items-center gap-1">
                    <AlertTriangle size={12} /> Warning
                </span>
            )}
        </div>
        <h3 className="text-2xl font-bold text-gray-900 leading-tight">
          {report.headline}
        </h3>
        {report.intent_vs_execution && (
           <ul className="mt-3 space-y-1">
             {report.intent_vs_execution.map((item, i) => (
               <li key={i} className="flex items-start gap-2 text-gray-700 text-sm">
                 <Target className="w-4 h-4 text-blue-500 mt-0.5 shrink-0" />
                 <span>{item}</span>
               </li>
             ))}
           </ul>
        )}
      </div>

      {/* 2. Key Metrics Grid */}
      <div className="bg-slate-50 rounded-lg p-4 border border-slate-100">
          <h4 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-3">Key Metrics</h4>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
             {report.key_metrics.map((metric, i) => (
                <div key={i} className="bg-white p-3 rounded shadow-sm border border-gray-100 flex items-center gap-2">
                   <Activity className="w-4 h-4 text-slate-400" />
                   <span className="text-sm font-medium text-slate-700">{metric}</span>
                </div>
             ))}
          </div>
      </div>

      {/* 3. Strengths & Opportunities */}
      <div className="grid md:grid-cols-2 gap-4">
          {/* Strengths */}
          <div className="bg-green-50/50 rounded-lg p-4 border border-green-100">
              <h4 className="text-sm font-semibold text-green-800 mb-2 flex items-center gap-2">
                  <CheckCircle2 className="w-4 h-4" /> Strong Points
              </h4>
              <ul className="space-y-2">
                  {report.strengths.map((s, i) => (
                      <li key={i} className="text-sm text-green-900 flex items-start gap-2">
                          <span className="mt-1.5 w-1 h-1 bg-green-500 rounded-full shrink-0"></span>
                          {s}
                      </li>
                  ))}
              </ul>
          </div>

          {/* Opportunities */}
          <div className="bg-amber-50/50 rounded-lg p-4 border border-amber-100">
               <h4 className="text-sm font-semibold text-amber-800 mb-2 flex items-center gap-2">
                  <TrendingUp className="w-4 h-4" /> Opportunities
              </h4>
              <ul className="space-y-2">
                  {report.opportunities.map((o, i) => (
                      <li key={i} className="text-sm text-amber-900 flex items-start gap-2">
                          <span className="mt-1.5 w-1 h-1 bg-amber-500 rounded-full shrink-0"></span>
                          {o}
                      </li>
                  ))}
              </ul>
          </div>
      </div>

      {/* 4. Weekly Focus & Warnings */}
      <div className="flex flex-col gap-4">
           {report.warnings.length > 0 && (
               <div className="bg-red-50 p-4 rounded-lg border border-red-200">
                    <h5 className="text-red-800 font-semibold text-sm mb-1 flex items-center gap-2">
                         <AlertTriangle className="w-4 h-4" /> Safety Check
                    </h5>
                    <ul className="list-disc list-inside text-sm text-red-700">
                        {report.warnings.map((w, i) => <li key={i}>{w}</li>)}
                    </ul>
               </div>
           )}
           
           <div className="bg-blue-50 p-4 rounded-lg border border-blue-100">
                <h5 className="text-blue-800 font-semibold text-sm mb-1 flex items-center gap-2">
                    <Calendar className="w-4 h-4" /> Weekly Focus
                </h5>
                <ul className="list-disc list-inside text-sm text-blue-700">
                     {report.weekly_focus.map((w, i) => <li key={i}>{w}</li>)}
                </ul>
           </div>
      </div>

      {/* 5. Next Run Presciption (Featured) */}
      <div className="mt-2 bg-gradient-to-r from-gray-900 to-gray-800 rounded-xl p-5 text-white shadow-lg">
          <div className="flex items-center gap-2 mb-3 text-gray-300 text-xs font-semibold uppercase tracking-wider">
              <Zap className="w-4 h-4 text-yellow-400" /> Recommended Next Session
          </div>
          <div className="mb-2">
              <span className="text-2xl font-bold text-white">{report.next_run.type}</span>
              <span className="mx-2 text-gray-400">•</span>
              <span className="text-xl text-gray-200">{report.next_run.duration}</span>
          </div>
          <div className="text-sm text-gray-300 italic mb-3">
              "{report.next_run.description}"
          </div>
          <div className="inline-block bg-gray-700 rounded px-2 py-1 text-xs font-mono text-yellow-300 border border-gray-600">
              Intensity: {report.next_run.intensity}
          </div>
      </div>

      {/* 6. Question */}
      {report.one_question && (
          <div className="flex items-start gap-3 p-4 bg-gray-50 rounded-lg border border-gray-200">
              <div className="bg-blue-100 text-blue-600 p-2 rounded-full">
                  <span className="text-lg font-bold">?</span>
              </div>
              <div>
                  <h5 className="font-semibold text-gray-900 text-sm">Coach asks:</h5>
                  <p className="text-gray-700 italic">{report.one_question}</p>
              </div>
          </div>
      )}

    </div>
  );
}
