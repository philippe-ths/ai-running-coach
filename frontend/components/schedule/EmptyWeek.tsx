"use client";

// #830: the week with no plan and nothing logged.
//
// The plan is WRITTEN BY THE COACH, not by a form — so the only affordance here
// is asking for one. Drafting is a slow LLM call on the worker, so the POST
// returns 202 and `useDraftStatus` polls, the `ImportStravaHistory` idiom. The
// status message is rendered verbatim: it is written for the runner, and
// paraphrasing it here would be a second voice speaking for the coach.

import { CalendarPlus, Loader2 } from "lucide-react";
import { useDraftStatus } from "@/lib/useDraftStatus";

export default function EmptyWeek({ onPlanReady }: { onPlanReady: () => void }) {
  const { draft, drafting, starting, error, start } = useDraftStatus(onPlanReady);

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
        onClick={() => void start()}
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
