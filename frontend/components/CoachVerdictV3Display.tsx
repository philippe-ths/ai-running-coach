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

export default function CoachVerdictV3Display({ verdict }: { verdict: CoachVerdictV3 }) {
    if (!verdict) return null;

    const statusClass = StatusColors[verdict.headline.status] || "bg-gray-100";

    return (
        <div className="space-y-6">
            {/* Meta */}
            <div className="text-xs text-slate-500 uppercase tracking-wide">
                {verdict.inputs_used_line}
            </div>

            {/* Headline */}
            <div className={`p-6 rounded-lg border-l-4 ${statusClass}`}>
                <h2 className="text-2xl font-bold tracking-tight">
                    {verdict.headline.sentence}
                </h2>
                <div className="mt-2 flex gap-4 text-sm opacity-90">
                    <span className="uppercase font-bold tracking-wider text-xs border px-2 py-0.5 rounded border-current opacity-80">
                        {verdict.headline.status}
                    </span>
                </div>
            </div>

            {/* Why It Matters */}
            <section>
                <h3 className="text-sm font-semibold uppercase text-slate-500 mb-3">Why It Matters</h3>
                <ul className="space-y-2">
                    {verdict.why_it_matters.map((point, idx) => (
                        <li key={idx} className="flex gap-3 text-slate-700">
                             <span className="text-blue-500 font-bold">•</span>
                             {point}
                        </li>
                    ))}
                </ul>
            </section>

            {/* Scorecard */}
            <section className="bg-slate-50 p-5 rounded-lg border border-slate-200">
                <h3 className="text-sm font-semibold uppercase text-slate-500 mb-4">Session Scorecard</h3>
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
            </section>

            {/* Run Story - 3 Acts */}
            <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
                 <div className="p-4 bg-white rounded border border-slate-200 shadow-sm">
                    <span className="block text-xs uppercase text-slate-400 font-bold mb-1">Act I: Start</span>
                    <p className="text-sm text-slate-700 leading-relaxed">{verdict.run_story.start}</p>
                 </div>
                 <div className="p-4 bg-white rounded border border-slate-200 shadow-sm">
                    <span className="block text-xs uppercase text-slate-400 font-bold mb-1">Act II: Middle</span>
                    <p className="text-sm text-slate-700 leading-relaxed">{verdict.run_story.middle}</p>
                 </div>
                 <div className="p-4 bg-white rounded border border-slate-200 shadow-sm">
                    <span className="block text-xs uppercase text-slate-400 font-bold mb-1">Act III: Finish</span>
                    <p className="text-sm text-slate-700 leading-relaxed">{verdict.run_story.finish}</p>
                 </div>
            </section>

            {/* The Lever */}
            <section className="bg-blue-50 p-6 rounded-lg border border-blue-100">
                 <h3 className="text-sm font-semibold uppercase text-blue-800 mb-2">Primary Lever: {verdict.lever.category}</h3>
                 <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 mt-4">
                     <div>
                         <div className="text-xs text-blue-500 mb-1">The Signal & Cause</div>
                         <p className="text-sm text-blue-900 font-medium">{verdict.lever.signal}</p>
                         <p className="text-sm text-blue-800 mt-1">{verdict.lever.cause}</p>
                     </div>
                     <div>
                         <div className="text-xs text-blue-500 mb-1">The Fix & Cue</div>
                         <p className="text-sm text-blue-900 font-medium">{verdict.lever.fix}</p>
                         <p className="text-sm text-blue-800 italic mt-1">"{verdict.lever.cue}"</p>
                     </div>
                 </div>
            </section>

            {/* Next Steps */}
            <section className="grid grid-cols-1 sm:grid-cols-2 gap-6">
                 <div>
                    <h3 className="text-sm font-semibold uppercase text-slate-500 mb-2">Tomorrow</h3>
                    <p className="text-lg font-medium text-slate-800">{verdict.next_steps.tomorrow}</p>
                 </div>
                 <div>
                    <h3 className="text-sm font-semibold uppercase text-slate-500 mb-2">Next 7 Days</h3>
                    <p className="text-lg font-medium text-slate-800">{verdict.next_steps.next_7_days}</p>
                 </div>
            </section>

             {/* Question */}
             {verdict.question_for_you && (
                <div className="bg-slate-900 text-slate-200 p-6 rounded-lg text-center mt-8">
                    <span className="block text-xs uppercase tracking-widest text-slate-400 mb-2">Coach asks</span>
                    <p className="text-xl font-light italic">"{verdict.question_for_you}"</p>
                </div>
             )}
        </div>
    );
}
