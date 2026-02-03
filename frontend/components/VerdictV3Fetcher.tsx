'use client';

import React, { useEffect, useState, useRef } from 'react';
import { CoachVerdictV3 } from '../lib/types';
import CoachVerdictV3Display from './CoachVerdictV3Display';
import { FEATURE_FLAGS } from '../lib/feature_flags';

interface VerdictV3FetcherProps {
    activityId: string;
    inputsKey: string;
    existingVerdict?: CoachVerdictV3 | null;
    children: React.ReactNode; 
}

// Helper to safely merge partial updates
type PartialVerdict = Partial<CoachVerdictV3>;

export default function VerdictV3Fetcher({ activityId, inputsKey, existingVerdict, children }: VerdictV3FetcherProps) {
    const [verdict, setVerdict] = useState<PartialVerdict | null>(existingVerdict || null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [statusMessage, setStatusMessage] = useState<string>("");

    // Prevent double-firing in React strict mode / rerenders
    const hasStartedRef = useRef(false);

    const storageKey = `verdict:v3:${activityId}:${inputsKey}`;
    
    // Consider verdict complete only when all sections required by CoachVerdictV3 exist
    const isComplete = Boolean(
        verdict &&
        verdict.headline &&
        verdict.scorecard &&
        verdict.run_story &&
        verdict.lever &&
        verdict.next_steps
    );

    // Only fetch if V3 is enabled and we don't already have a complete verdict.
    const shouldFetch = FEATURE_FLAGS.VERDICT_V3 && !isComplete && !error;

    useEffect(() => {
        // When inputsKey changes, attempt to hydrate from localStorage.
        // This ensures a hard refresh does NOT re-trigger generation unless intent/check-in changed.
        setError(null);
        setLoading(false);
        setStatusMessage("");
        hasStartedRef.current = false;

        if (existingVerdict) {
            setVerdict(existingVerdict);
            try {
                localStorage.setItem(storageKey, JSON.stringify(existingVerdict));
            } catch {
                // ignore storage errors
            }
            return;
        }

        try {
            const cached = localStorage.getItem(storageKey);
            if (cached) {
                const parsed = JSON.parse(cached);
                setVerdict(parsed);
            } else {
                setVerdict(null);
            }
        } catch {
            setVerdict(null);
        }
    }, [activityId, inputsKey, existingVerdict, storageKey]);

    useEffect(() => {
        if (!shouldFetch || hasStartedRef.current) return;

        // Mark as started immediately to avoid duplicate orchestration
        hasStartedRef.current = true;

        let isMounted = true;
        setLoading(true);
        const controller = new AbortController();

        const apiBase = '/api/verdict/v3'; 
        // Note: next.config.js now proxies /api -> http://localhost:8000/api
        
        async function orchestrateGeneration() {
            // Setup timeout (45s total to be safe)
            const timeoutId = setTimeout(() => controller.abort(), 45000);

            try {
                const body = { activity_id: activityId };
                const headers = { 'Content-Type': 'application/json' };
                // Include defaults, but allow specific calls to override body
                const fetchOpts = { 
                    method: 'POST', 
                    headers, 
                    cache: 'no-store' as const,
                    body: JSON.stringify(body),
                    signal: controller.signal
                };

                // 1. Scorecard (Critical)
                console.log("[V3] Fetching Scorecard...");
                setStatusMessage("Analyzing run data (Step 1/5)...");
                const scorecardRes = await fetch(`${apiBase}/scorecard`, fetchOpts);
                console.log(`[V3] Scorecard Status: ${scorecardRes.status}`);
                
                if (!scorecardRes.ok) {
                    const txt = await scorecardRes.text();
                    console.error("[V3] Scorecard Failed:", txt);
                    throw new Error(`Scorecard failed (${scorecardRes.status}): ${txt}`);
                }
                const scorecardData = await scorecardRes.json();
                console.log("[V3] Scorecard Data received:", scorecardData);
                if (!isMounted) return;
                
                // Update Step 1
                setVerdict(prev => ({ 
                    ...prev, 
                    ...scorecardData 
                }));

                // 2. Story (Parallel-ish or Sequential)
                setStatusMessage("Drafting narrative (Step 2/5)...");
                const storyRes = await fetch(`${apiBase}/story`, fetchOpts);
                if (storyRes.ok) {
                    const storyData = await storyRes.json();
                    if (isMounted) setVerdict(prev => ({ ...prev, ...storyData }));
                }

                // 3. Lever (Requires Scorecard)
                setStatusMessage("Identifying key levers (Step 3/5)...");
                // Ensure we pass the EXACT structure expected by Pydantic
                const leverReq = { 
                    activity_id: activityId, 
                    scorecard: scorecardData 
                };
                
                const leverRes = await fetch(`${apiBase}/lever`, { 
                    ...fetchOpts, 
                    body: JSON.stringify(leverReq) 
                });

                if (!leverRes.ok) throw new Error("Lever generation failed");
                const leverData = await leverRes.json();
                if (!isMounted) return;
                setVerdict(prev => ({ ...prev, ...leverData }));

                // 4. Question (Requires Scorecard)
                setStatusMessage("Finalizing report (Step 4/5)...");
                const questionRes = await fetch(`${apiBase}/question`, {
                    ...fetchOpts,
                    body: JSON.stringify(leverReq)
                });
                if (questionRes.ok) {
                    const qData = await questionRes.json();
                    if (isMounted) setVerdict(prev => ({ ...prev, ...qData }));
                }

                // 5. Next Steps (Requires Scorecard + Lever)
                setStatusMessage("Planning schedule (Step 5/5)...");
                // Ensure we pass the EXACT structure expected by Pydantic
                // The leverData likely contains { lever: { ... } } 
                // schema expects "lever": LeverResponse (which wraps .lever)
                // So we pass leverData directly as "lever"
                const nextStepsReq = { 
                    activity_id: activityId, 
                    scorecard: scorecardData, 
                    lever: leverData // The response IS the LeverResponse object
                };

                const nextStepsRes = await fetch(`${apiBase}/next-steps`, { 
                    ...fetchOpts, 
                    body: JSON.stringify(nextStepsReq)
                });

                if (!nextStepsRes.ok) throw new Error("Next steps generation failed");
                const nextStepsData = await nextStepsRes.json();

                if (!isMounted) return;
                setVerdict(prev => {
                    const merged = { ...(prev ?? {}), ...nextStepsData };
                    try {
                        localStorage.setItem(storageKey, JSON.stringify(merged));
                    } catch {
                        // ignore storage errors
                    }
                    return merged;
                });
                setLoading(false);

            } catch (err: any) {
                console.error("V3 Orchestration Error:", err);
                if (isMounted) {
                    setError(err.message || "Unknown error");
                    setLoading(false);
                }
            }
        }

        orchestrateGeneration();

        return () => { 
            isMounted = false; 
            controller.abort(); // Cancel in-flight request on unmount/re-run
        };
    }, [activityId, shouldFetch, storageKey]);

    if (!FEATURE_FLAGS.VERDICT_V3) {
        return <>{children}</>;
    }

    if (error) {
        return (
            <div className="p-4 bg-red-50 border border-red-200 rounded-lg">
                <p className="text-sm text-red-700 font-semibold mb-1">Analysis Failed</p>
                <p className="text-xs text-red-600 font-mono">{error}</p>
                <button 
                    onClick={() => { setError(null); hasStartedRef.current = false; }}
                    className="mt-2 text-xs text-red-800 underline"
                >
                    Retry
                </button>
            </div>
        );
    }

    if (isComplete) {
        return <CoachVerdictV3Display verdict={verdict as CoachVerdictV3} />;
    }

    // Loading State
    return (
        <div className="animate-pulse space-y-6">
            <div className="flex justify-between items-center">
                <div className="h-4 bg-gray-200 rounded w-1/4"></div>
                <span className="text-xs text-gray-400 font-mono">{statusMessage}</span>
            </div>
            <div className="h-24 bg-gray-200 rounded-lg border-l-4 border-gray-300"></div>
            <div className="space-y-2">
                <div className="h-4 bg-gray-200 rounded w-3/4"></div>
                <div className="h-4 bg-gray-200 rounded w-1/2"></div>
            </div>
            <div className="h-48 bg-gray-200 rounded-lg"></div>
        </div>
    );
}
