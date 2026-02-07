import React from 'react';
import { CoachVerdictV3 } from '../lib/types';

// Simple inline styles to avoid prop drilling complex tailwind if UI lib missing
const StatusColors = {
    green: "bg-green-100 text-green-800 border-green-200",
    amber: "bg-yellow-100 text-yellow-800 border-yellow-200",
    red: "bg-red-100 text-red-800 border-red-200"
};

const RatingColors = {
    ok: "text-green-600 font-medium",
    warn: "text-yellow-600 font-medium",
    fail: "text-red-600 font-bold",
    unknown: "text-gray-400"
};

function Panel({ 
    title, 
    children, 
    className,
    contextSource,
    debugValues
}: { 
    title?: React.ReactNode, 
    children: React.ReactNode, 
    className?: string,
    contextSource?: string,
    debugValues?: {
        context?: any,
        prompt?: string
    }
}) {
    const finalClass = className || "bg-white p-6 rounded-xl border border-slate-200 shadow-sm";
    
    return (
        <section className={finalClass}>
            {title && (
                <h3 className="text-sm font-bold uppercase text-slate-400 tracking-wider mb-4">
                    {title}
                </h3>
            )}
            {children}
            {contextSource && (
                 <div className="mt-6 pt-3 border-t border-black/5 text-[10px] text-slate-400 uppercase tracking-widest font-mono">
                    <div className="mb-2">Context: {contextSource}</div>
                    
                    {debugValues && (debugValues.context || debugValues.prompt) && (
                        <details className="group">
                            <summary className="cursor-pointer list-none hover:text-slate-600 transition-colors inline-flex items-center gap-1 select-none">
                                <span className="opacity-50 group-hover:opacity-100">▶</span>
                                <span className="underline decoration-dotted underline-offset-2">Raw Data & Prompt</span>
                            </summary>
                            <div className="mt-3 bg-slate-950 text-slate-300 p-4 rounded overflow-auto max-h-[400px] text-[10px] leading-tight font-mono whitespace-pre normal-case tracking-normal">
                                {debugValues.prompt && (
                                    <div className="mb-6 border-b border-slate-800 pb-4">
                                        <div className="text-emerald-400 font-bold mb-2 uppercase text-[9px] tracking-wider">Generated Prompt</div>
                                        {debugValues.prompt}
                                    </div>
                                )}
                                {debugValues.context && (
                                    <div>
                                        <div className="text-blue-400 font-bold mb-2 uppercase text-[9px] tracking-wider">Context Pack</div>
                                        {JSON.stringify(debugValues.context, null, 2)}
                                    </div>
                                )}
                            </div>
                        </details>
                    )}
                </div>
            )}
        </section>
    );
}

export default function CoachVerdictV3Display({ verdict }: { verdict: CoachVerdictV3 }) {
    if (!verdict) return null;

    // Helper to extract debug info safely
    const getDebug = (promptKey: string) => ({
        context: verdict.debug_context,
        prompt: verdict.debug_prompt?.[promptKey]
    });

    const summaryDebug = getDebug('summary');

    // Use Executive Summary if available, fallback to headline (legacy)
    const summary = verdict.executive_summary;
    const headline = verdict.headline;

    // Determine status and title from either source
    const status = summary?.status || headline?.status || "green"; // Default green if missing
    const title = summary?.title || headline?.sentence || "Session Analysis";
    const opinion = summary?.opinion;

    const statusClass = StatusColors[status] || "bg-gray-100";

    return (
        <div className="space-y-8">
            {/* Headline / Summary Panel */}
            <div className={`p-6 rounded-xl border-l-4 shadow-sm ${statusClass}`}>
                <h2 className="text-2xl font-bold tracking-tight">
                    {title}
                </h2>
                
                {opinion && (
                    <div className="mt-4 text-sm leading-relaxed opacity-90 border-t border-black/10 pt-3">
                         <p className="font-medium mb-1 text-xs uppercase tracking-wide opacity-70">Coach Verdict</p>
                         <p>{opinion}</p>
                    </div>
                )}

                <div className="mt-4 flex gap-4 text-sm opacity-90 items-center justify-between">
                    <span className="uppercase font-bold tracking-wider text-xs border px-2 py-0.5 rounded border-current opacity-80">
                        {status}
                    </span>
                    <span className="text-[10px] uppercase font-mono opacity-60">
                         Context: Full Report
                    </span>
                </div>

                {/* Debug for Summary */}
                {(summaryDebug.context || summaryDebug.prompt) && (
                    <div className="mt-6 pt-3 border-t border-black/5 text-[10px] text-slate-500 uppercase tracking-widest font-mono opacity-90">
                        <details className="group">
                            <summary className="cursor-pointer list-none hover:text-slate-800 transition-colors inline-flex items-center gap-1 select-none">
                                <span className="opacity-50 group-hover:opacity-100">▶</span>
                                <span className="underline decoration-dotted underline-offset-2">Raw Data & Prompt</span>
                            </summary>
                            <div className="mt-3 bg-slate-950 text-slate-300 p-4 rounded overflow-auto max-h-[400px] text-[10px] leading-tight font-mono whitespace-pre normal-case tracking-normal">
                                {summaryDebug.prompt && (
                                    <div className="mb-6 border-b border-slate-800 pb-4">
                                        <div className="text-emerald-400 font-bold mb-2 uppercase text-[9px] tracking-wider">Generated Prompt</div>
                                        {summaryDebug.prompt}
                                    </div>
                                )}
                                {summaryDebug.context && (
                                    <div>
                                        <div className="text-blue-400 font-bold mb-2 uppercase text-[9px] tracking-wider">Context Pack</div>
                                        {JSON.stringify(summaryDebug.context, null, 2)}
                                    </div>
                                )}
                            </div>
                        </details>
                    </div>
                )}
            </div>

            {/* Why It Matters */}
            <Panel 
                title="Why It Matters"
                contextSource="Activity Metrics, Athlete Profile, Analysis Flags, Check-in"
                debugValues={getDebug('scorecard')}
            >
                <ul className="space-y-2">
                    {verdict.why_it_matters.map((point, idx) => (
                        <li key={idx} className="flex gap-3 text-slate-700">
                             <span className="text-blue-500 font-bold">•</span>
                             {point}
                        </li>
                    ))}
                </ul>
            </Panel>

            {/* Scorecard */}
            <Panel 
                title="Session Scorecard" 
                className="bg-slate-50 p-6 rounded-xl border border-slate-200"
                contextSource="Activity Metrics, Athlete Profile, Analysis Flags, Check-in"
                debugValues={getDebug('scorecard')}
            >
                <div className="space-y-3">
                    {verdict.scorecard.map((row, idx) => (
                        <div key={idx} className="grid grid-cols-1 sm:grid-cols-[200px_80px_1fr] gap-1 sm:gap-4 items-baseline border-b border-slate-200 last:border-0 pb-3 last:pb-0">
                            <span className="font-medium text-slate-700">{row.item}</span>
                            <span className={`${RatingColors[row.rating]} uppercase text-xs tracking-wider font-bold`}>
                                {row.rating}
                            </span>
                            <span className="text-sm text-slate-600 leading-snug">{row.reason}</span>
                        </div>
                    ))}
                </div>
            </Panel>

            {/* Run Story - 3 Acts */}
            <Panel 
                title="Run Story"
                contextSource="Activity Metadata, RPE & Notes, Key Evidence"
                debugValues={getDebug('story')}
            >
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    <div>
                        <span className="block text-xs uppercase text-slate-400 font-bold mb-2">Act I: Start</span>
                        <p className="text-sm text-slate-700 leading-relaxed bg-slate-50 p-4 rounded-lg border border-slate-100 shadow-sm">{verdict.run_story.start}</p>
                    </div>
                    <div>
                        <span className="block text-xs uppercase text-slate-400 font-bold mb-2">Act II: Middle</span>
                        <p className="text-sm text-slate-700 leading-relaxed bg-slate-50 p-4 rounded-lg border border-slate-100 shadow-sm">{verdict.run_story.middle}</p>
                    </div>
                    <div>
                        <span className="block text-xs uppercase text-slate-400 font-bold mb-2">Act III: Finish</span>
                        <p className="text-sm text-slate-700 leading-relaxed bg-slate-50 p-4 rounded-lg border border-slate-100 shadow-sm">{verdict.run_story.finish}</p>
                    </div>
                </div>
            </Panel>

            {/* The Lever */}
            <Panel 
                title={<>Primary Lever: <span className="text-blue-600">{verdict.lever.category}</span></>} 
                className="bg-blue-50 p-6 rounded-xl border border-blue-100"
                contextSource="Scorecard Weaknesses, Diagnostic Flags, Experience Level"
                debugValues={getDebug('lever')}
            >
                 <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
                     <div>
                         <div className="text-xs font-bold uppercase tracking-wider text-blue-400 mb-1">The Signal & Cause</div>
                         <p className="text-sm text-blue-900 font-medium">{verdict.lever.signal}</p>
                         <p className="text-sm text-blue-800 mt-1">{verdict.lever.cause}</p>
                     </div>
                     <div>
                         <div className="text-xs font-bold uppercase tracking-wider text-blue-400 mb-1">The Fix & Cue</div>
                         <p className="text-sm text-blue-900 font-medium">{verdict.lever.fix}</p>
                         <p className="text-sm text-blue-800 italic mt-1 bg-white/50 p-2 rounded border border-blue-100">"{verdict.lever.cue}"</p>
                     </div>
                 </div>
            </Panel>

            {/* Next Steps */}
            <Panel 
                title="Looking Ahead"
                contextSource="Verdict, Fatigue Status, Last 7 Days Load, Lever Focus"
                debugValues={getDebug('next_steps')}
            >
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
                    <div>
                        <h4 className="text-xs font-bold uppercase text-slate-400 mb-2">Tomorrow</h4>
                        <p className="text-lg font-medium text-slate-800">{verdict.next_steps.tomorrow}</p>
                    </div>
                    <div>
                        <h4 className="text-xs font-bold uppercase text-slate-400 mb-2">Next 7 Days</h4>
                        <p className="text-lg font-medium text-slate-800">{verdict.next_steps.next_7_days}</p>
                    </div>
                </div>
            </Panel>

             {/* Question */}
             {verdict.question_for_you && (
                <div className="bg-slate-900 text-slate-200 p-6 rounded-xl text-center shadow-lg">
                    <span className="block text-xs uppercase tracking-widest text-slate-400 mb-3">Coach asks</span>
                    <p className="text-xl font-light italic mb-6">"{verdict.question_for_you}"</p>
                    <div className="pt-3 border-t border-slate-700 text-[10px] text-slate-500 uppercase tracking-widest font-mono">
                        <div className="mb-2">Context: User Notes, Verdict Theme, Pain Points</div>
                        {getDebug('question') && (
                            <details className="group text-left">
                                <summary className="cursor-pointer list-none hover:text-slate-300 transition-colors inline-flex items-center gap-1 select-none justify-center w-full">
                                    <span className="opacity-50 group-hover:opacity-100">▶</span>
                                    <span className="underline decoration-dotted underline-offset-2">Raw Data & Prompt</span>
                                </summary>
                                <div className="mt-3 bg-black/50 text-slate-300 p-4 rounded overflow-auto max-h-[400px] text-[10px] leading-tight font-mono whitespace-pre normal-case tracking-normal">
                                    {getDebug('question').prompt && (
                                        <div className="mb-6 border-b border-slate-800 pb-4">
                                            <div className="text-emerald-400 font-bold mb-2 uppercase text-[9px] tracking-wider">Generated Prompt</div>
                                            {getDebug('question').prompt}
                                        </div>
                                    )}
                                    {getDebug('question').context && (
                                        <div>
                                            <div className="text-blue-400 font-bold mb-2 uppercase text-[9px] tracking-wider">Context Pack</div>
                                            {JSON.stringify(getDebug('question').context, null, 2)}
                                        </div>
                                    )}
                                </div>
                            </details>
                        )}
                    </div>
                </div>
             )}
        </div>
    );
}
