"use client";

// #830: the week with no plan and nothing logged.
//
// The plan is WRITTEN BY THE COACH, not by a form — so the only affordance here
// is asking for one. Drafting is a slow LLM call on the worker, so the POST
// returns 202 and this polls, the `ImportStravaHistory` idiom. The status
// message is rendered verbatim: it is written for the runner, and paraphrasing
// it here would be a second voice speaking for the coach.

import { useCallback, useEffect, useRef, useState } from "react";
import { CalendarPlus, Loader2 } from "lucide-react";
import { fetchFromAPI } from "@/lib/api";
import type { DraftStatus } from "@/lib/types/schedule";

const POLL_INTERVAL_MS = 3000;

export default function EmptyWeek({ onPlanReady }: { onPlanReady: () => void }) {
  const [draft, setDraft] = useState<DraftStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The callback identity changes with the parent's week state; a ref keeps the
  // poll loop from re-subscribing (and double-polling) every time it does.
  const ready = useRef(onPlanReady);
  ready.current = onPlanReady;

  const poll = useCallback(async () => {
    try {
      const data: DraftStatus | null = await fetchFromAPI("/api/schedule/draft");
      setDraft(data);
      if (data?.status === "drafting") {
        timer.current = setTimeout(poll, POLL_INTERVAL_MS);
      } else if (data?.status === "active") {
        ready.current();
      }
    } catch {
      // A failed poll is not worth a red banner: the next tap re-reads it.
    }
  }, []);

  // On mount, pick up a draft already in flight (asked for on another device,
  // or before a reload) and resume polling it.
  useEffect(() => {
    poll();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [poll]);

  const drafting = draft?.status === "drafting";

  const startDraft = async () => {
    if (starting || drafting) return;
    setStarting(true);
    setError(null);
    try {
      const data: DraftStatus = await fetchFromAPI("/api/schedule/draft", {
        method: "POST",
      });
      setDraft(data);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(poll, POLL_INTERVAL_MS);
    } catch {
      setError("Could not ask your coach for a plan just now. Nothing has changed — try again.");
    } finally {
      setStarting(false);
    }
  };

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-700 dark:bg-gray-800">
      <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
        Nothing on this week yet
      </h2>
      <p className="mt-2 max-w-prose text-sm text-gray-600 dark:text-gray-400">
        Your coach writes the plan — you do not fill in a form. Ask for one and it
        will be built from your own history, your goal and the rules you have
        agreed on. Until then this page just shows what you actually do.
      </p>

      <button
        type="button"
        onClick={startDraft}
        disabled={starting || drafting}
        className="mt-4 inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
      >
        {starting || drafting ? (
          <Loader2 size={16} className="animate-spin" aria-hidden="true" />
        ) : (
          <CalendarPlus size={16} aria-hidden="true" />
        )}
        {drafting ? "Writing your plan…" : starting ? "Asking…" : "Have your coach draft a plan"}
      </button>

      {/* Verbatim, but only for the two statuses that are ABOUT the request in
          flight. "Your plan is ready." printed under "Nothing on this week yet"
          is a contradiction the runner has to resolve — and it is reachable, since
          a plan can be active while covering later weeks than this one. When it
          IS ready, the refetch above replaces this whole panel. */}
      {(draft?.status === "drafting" || draft?.status === "failed") && (
        <p
          className={`mt-3 text-sm ${
            draft.status === "failed"
              ? "text-amber-700 dark:text-amber-300"
              : "text-gray-600 dark:text-gray-400"
          }`}
        >
          {draft.message}
        </p>
      )}

      {error && (
        <p className="mt-3 text-sm text-red-600 dark:text-red-400">{error}</p>
      )}
    </section>
  );
}
